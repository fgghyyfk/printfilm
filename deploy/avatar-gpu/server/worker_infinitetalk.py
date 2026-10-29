"""InfiniteTalk 常驻工作进程：模型只加载一次，之后逐个处理「照片 + 音频 → 对口型视频」任务。

流程取自官方 generate_infinitetalk.py 的单人、本地音频、streaming 分支，固定用 lightx2v 4 步加速 LoRA。
需在 InfiniteTalk 仓库目录下、multitalk 环境中运行（由 server.py 拉起）；仓库须已打 patch_infinitetalk_lowmem.py。
环境变量：AVATAR_LOW_VRAM=1 时 DiT 按需换入显存、T5 留 CPU（24GB 卡必需）。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

import generate_infinitetalk as gi  # noqa: E402  官方脚本，导入时只执行顶层 import
import wan  # noqa: E402
from wan.configs import WAN_CONFIGS  # noqa: E402
from wan.utils.multitalk_utils import save_video_ffmpeg  # noqa: E402
from worker_common import serve  # noqa: E402

FPS = 25
LOW_VRAM = os.environ.get("AVATAR_LOW_VRAM") == "1"


def build_args():
    """复用官方参数解析得到完整 args（pipeline 内部会读其中的若干开关）。"""
    argv = [
        "generate_infinitetalk.py",
        "--ckpt_dir", "weights/Wan2.1-I2V-14B-480P",
        "--wav2vec_dir", "weights/chinese-wav2vec2-base",
        "--infinitetalk_dir", "weights/InfiniteTalk/single/infinitetalk.safetensors",
        "--input_json", "unused.json",
        "--mode", "streaming", "--motion_frame", "9",
        "--lora_dir", "weights/lora/Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
        "--lora_scale", "1.0", "--sample_text_guide_scale", "1.0", "--sample_audio_guide_scale", "2.0",
        "--sample_steps", "4", "--sample_shift", "2", "--base_seed", "42",
    ]
    if LOW_VRAM:
        argv += ["--num_persistent_param_in_dit", os.environ.get("INFINITETALK_PERSIST", "0"), "--t5_cpu"]
    sys.argv = argv
    args = gi._parse_args()
    gi._validate_args(args)
    args.offload_model = True
    return args


def load():
    """加载 Wan2.1 + InfiniteTalk + LoRA 与 wav2vec 音频编码器。"""
    args = build_args()
    pipe = wan.InfiniteTalkPipeline(
        config=WAN_CONFIGS[args.task], checkpoint_dir=args.ckpt_dir, quant_dir=args.quant_dir,
        device_id=0, rank=0, t5_fsdp=False, dit_fsdp=False, use_usp=False, t5_cpu=args.t5_cpu,
        lora_dir=args.lora_dir, lora_scales=args.lora_scale, quant=args.quant,
        dit_path=args.dit_path, infinitetalk_dir=args.infinitetalk_dir,
    )
    if args.num_persistent_param_in_dit is not None:
        pipe.vram_management = True
        pipe.enable_vram_management(num_persistent_param_in_dit=args.num_persistent_param_in_dit)
    extractor, encoder = gi.custom_init("cpu", args.wav2vec_dir)
    return args, pipe, extractor, encoder


def main() -> None:
    """加载模型后进入任务循环。"""
    args, pipe, extractor, encoder = load()

    def handle(job: dict, report) -> None:
        report(0.05, "提取音频特征")
        work = tempfile.mkdtemp(prefix=f"it_{job['id']}_")
        speech = gi.audio_prepare_single(job["audio"])
        seconds = len(speech) / 16000
        # 模型每次至少生成 frame_num 帧（81 帧约 3.2 秒），音频不够长会断言失败：补静音，生成后再裁回原长
        min_samples = math.ceil((args.frame_num + 5) / FPS * 16000)
        if len(speech) < min_samples:
            speech = np.concatenate([speech, np.zeros(min_samples - len(speech), dtype=speech.dtype)])
        emb = gi.get_embedding(speech, extractor, encoder)
        emb_path = os.path.join(work, "1.pt")
        wav_path = os.path.join(work, "sum.wav")
        torch.save(emb, emb_path)
        sf.write(wav_path, speech, 16000)
        gen_seconds = len(speech) / 16000
        size = "infinitetalk-720" if job.get("resolution") == "720p" else "infinitetalk-480"
        clip = {
            "prompt": job.get("prompt") or "A person is talking to the camera.",
            "cond_video": job["image"],
            "cond_audio": {"person1": emb_path},
            "video_audio": wav_path,
        }
        report(0.1, f"生成中（音频 {seconds:.1f} 秒）")
        video = pipe.generate_infinitetalk(
            clip, size_buckget=size, motion_frame=args.motion_frame, frame_num=args.frame_num,
            shift=args.sample_shift, sampling_steps=args.sample_steps,
            text_guide_scale=args.sample_text_guide_scale, audio_guide_scale=args.sample_audio_guide_scale,
            seed=args.base_seed, offload_model=args.offload_model,
            max_frames_num=math.ceil(gen_seconds * FPS) + 25,
            color_correction_strength=args.color_correction_strength, extra_args=args,
        )
        report(0.95, "编码视频")
        out = job["out"]
        if gen_seconds - seconds < 0.05:
            save_video_ffmpeg(video, out[:-4] if out.endswith(".mp4") else out, [wav_path], high_quality_save=False)
            return
        # 补过静音：先存完整视频，再裁到原音频时长并换回原音频
        full = os.path.join(work, "full")
        save_video_ffmpeg(video, full, [wav_path], high_quality_save=False)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", full + ".mp4", "-i", job["audio"], "-map", "0:v",
                        "-map", "1:a", "-t", f"{seconds:.3f}", "-c:v", "libx264", "-crf", "18", "-c:a", "aac", out],
                       check=True)

    if os.environ.get("AVATAR_WARMUP") == "1":
        warmup(handle)
    serve(handle)


def warmup(handle) -> None:
    """预热：用灰图 + 3.5 秒轻噪声空跑一遍。

    低显存模式下加载时部分权重被挤进 swap，第一次推理要逐层换回（3090 实测首段 29 分钟、之后约 6 分钟），
    放在启动阶段做完，第一个真实任务就不用多等。失败只记日志。
    """
    from PIL import Image

    work = tempfile.mkdtemp(prefix="it_warmup_")
    img, wav = os.path.join(work, "w.png"), os.path.join(work, "w.wav")
    Image.new("RGB", (480, 832), (128, 128, 128)).save(img)
    sf.write(wav, (np.random.randn(int(3.5 * 16000)) * 0.003).astype("float32"), 16000)
    try:
        handle({"id": "warmup", "image": img, "audio": wav, "out": os.path.join(work, "w.mp4")},
               lambda *_a, **_k: None)
        print("预热完成", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"预热失败（不影响服务）：{exc}", flush=True)


if __name__ == "__main__":
    print(json.dumps({"engine": "infinitetalk", "low_vram": LOW_VRAM}), flush=True)
    main()
