"""LongCat-Video-Avatar 1.5 常驻工作进程：模型只加载一次，之后逐个处理「照片 + 音频 → 对口型视频」任务。

流程取自官方 run_demo_avatar_single_audio_to_video.py 的 ai2v + 分段续写（8 步蒸馏 + INT8），单卡运行。
需在 LongCat-Video 仓库目录下、longcat-video 环境中运行（由 server.py 拉起）。
AVATAR_LOW_VRAM=1 时：仓库须已打 patch_longcat_lowvram.py（server 启动脚本会打），
文本编码器与 whisper 留 CPU、DiT 按层换入显存、续写 KV 缓存放内存——与 3090 实测方案一致。
"""
from __future__ import annotations

import datetime
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOW_VRAM = os.environ.get("AVATAR_LOW_VRAM") == "1"
if LOW_VRAM:
    # 补丁里的分支按这些变量生效（见 patch_longcat_lowvram.py）
    os.environ["LONGCAT_TE_CPU"] = "1"
    os.environ.setdefault("LONGCAT_DIT_OFFLOAD", "1")
    os.environ.setdefault("LONGCAT_RESIDENT_BLOCKS", "4")
for k, v in {"RANK": "0", "WORLD_SIZE": "1", "LOCAL_RANK": "0", "MASTER_ADDR": "127.0.0.1",
             "MASTER_PORT": os.environ.get("LONGCAT_MASTER_PORT", "29531")}.items():
    os.environ.setdefault(k, v)

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import PIL.Image  # noqa: E402
import torch  # noqa: E402
import torch.distributed as dist  # noqa: E402
from audio_separator.separator import Separator  # noqa: E402
from diffusers.utils import load_image  # noqa: E402
from transformers import AutoTokenizer, UMT5EncoderModel  # noqa: E402

from longcat_video.audio_process import get_audio_encoder, get_audio_feature_extractor  # noqa: E402
from longcat_video.audio_process.torch_utils import save_video_ffmpeg  # noqa: E402
from longcat_video.context_parallel import context_parallel_util  # noqa: E402
from longcat_video.modules.autoencoder_kl_wan import AutoencoderKLWan  # noqa: E402
from longcat_video.modules.quantization import load_quantized_dit  # noqa: E402
from longcat_video.modules.scheduling_flow_match_euler_discrete import FlowMatchEulerDiscreteScheduler  # noqa: E402
from longcat_video.pipeline_longcat_video_avatar import LongCatVideoAvatarPipeline  # noqa: E402
from worker_common import serve  # noqa: E402

CKPT = "./weights/LongCat-Video-Avatar-1.5"
BASE = "./weights/LongCat-Video"
MODEL_TYPE = "avatar-v1.5"
FPS, NUM_FRAMES, NUM_COND = 25, 93, 13
NEG = ("Close-up, Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, "
       "static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
       "poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, "
       "messy background, three legs, many people in the background, walking backwards")


def load():
    """初始化单卡分布式环境并加载全部模型，返回 (pipe, vocal_separator, separator_dir)。"""
    torch.cuda.set_device(0)
    dist.init_process_group(backend="nccl", timeout=datetime.timedelta(hours=24))
    context_parallel_util.init_context_parallel(context_parallel_size=1, global_rank=0, world_size=1)
    split_hw = context_parallel_util.get_optimal_split(1)

    tokenizer = AutoTokenizer.from_pretrained(BASE, subfolder="tokenizer", torch_dtype=torch.bfloat16)
    text_encoder = UMT5EncoderModel.from_pretrained(BASE, subfolder="text_encoder", torch_dtype=torch.bfloat16)
    vae = AutoencoderKLWan.from_pretrained(BASE, subfolder="vae", torch_dtype=torch.bfloat16)
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(CKPT, subfolder="scheduler", torch_dtype=torch.bfloat16)
    dit = load_quantized_dit(CKPT, subfolder="base_model_int8", cp_split_hw=split_hw)
    lora = os.path.join(CKPT, "lora", "dmd_lora.safetensors")
    dit.load_lora(lora, "dmd", multiplier=1.0, lora_network_dim=128, lora_network_alpha=64)
    dit.enable_loras(["dmd"])

    whisper = os.path.join(CKPT, "whisper-large-v3")
    audio_encoder = get_audio_encoder(whisper, MODEL_TYPE).to("cpu" if LOW_VRAM else 0)
    extractor = get_audio_feature_extractor(whisper, MODEL_TYPE)

    sep_dir = tempfile.mkdtemp(prefix="lc_vocal_")
    separator = Separator(output_dir=os.path.join(sep_dir, "vocals"), output_single_stem="vocals",
                          model_file_dir=os.path.join(CKPT, "vocal_separator"))
    separator.load_model("Kim_Vocal_2.onnx")

    pipe = LongCatVideoAvatarPipeline(tokenizer=tokenizer, text_encoder=text_encoder, vae=vae, scheduler=scheduler,
                                      dit=dit, audio_encoder=audio_encoder, audio_feature_extractor=extractor,
                                      model_type=MODEL_TYPE)
    if LOW_VRAM:
        pipe.keep_te_cpu = True
        pipe.text_encoder.to(torch.float32)
    pipe.to(0)
    return pipe, separator, sep_dir


def to_frames(output) -> list:
    """管线输出（float 0~1 数组）转 PIL 帧列表。"""
    return [PIL.Image.fromarray((output[i] * 255).astype(np.uint8)) for i in range(output.shape[0])]


def main() -> None:
    """加载模型后进入任务循环。"""
    pipe, separator, sep_dir = load()

    def handle(job: dict, report) -> None:
        report(0.02, "分离人声")
        # 人声分离：TTS 音频本身很干净，分离失败就直接用原音频
        vocal = job["audio"]
        try:
            outs = separator.separate(job["audio"])
            if outs:
                cand = os.path.join(sep_dir, "vocals", outs[0])
                vocal = cand if os.path.exists(cand) else vocal
        except Exception as exc:  # noqa: BLE001
            print(f"人声分离失败，使用原音频：{exc}", flush=True)
        speech, sr = librosa.load(vocal, sr=16000)
        seconds = len(speech) / sr
        segs = max(1, math.ceil((seconds - NUM_FRAMES / FPS) / ((NUM_FRAMES - NUM_COND) / FPS)) + 1)
        target = NUM_FRAMES / FPS + (segs - 1) * (NUM_FRAMES - NUM_COND) / FPS
        pad = math.ceil((target - seconds) * sr)
        if pad > 0:
            speech = np.append(speech, [0.0] * pad)
        if vocal != job["audio"] and os.path.exists(vocal):
            os.remove(vocal)

        full_emb = pipe.get_audio_embedding(speech, fps=FPS, device=0, sample_rate=sr, model_type=MODEL_TYPE)
        if torch.isnan(full_emb).any():
            raise ValueError("音频特征含 NaN")
        idx = torch.arange(5) - 2

        def window(start: int):
            centers = torch.arange(start, start + NUM_FRAMES).unsqueeze(1) + idx.unsqueeze(0)
            centers = torch.clamp(centers, min=0, max=full_emb.shape[0] - 1)
            return full_emb[centers][None, ...].to(0)

        prompt = job.get("prompt") or "A person is talking to the camera."
        gen = torch.Generator(device=0).manual_seed(42)
        report(0.05, f"生成第 1/{segs} 段")
        output, latent = pipe.generate_ai2v(
            image=load_image(job["image"]), prompt=prompt, negative_prompt=NEG,
            resolution=job.get("resolution") or "480p", num_frames=NUM_FRAMES, num_inference_steps=8,
            text_guidance_scale=1.0, audio_guidance_scale=1.0, output_type="both", generator=gen,
            audio_emb=window(0), use_distill=True,
        )
        video = to_frames(output[0])
        frames, current, ref_latent = list(video), video, latent[:, :, :1].clone()
        width, height = video[0].size
        start = 0
        for s in range(1, segs):
            report(0.05 + 0.9 * s / segs, f"生成第 {s + 1}/{segs} 段")
            start += NUM_FRAMES - NUM_COND
            output, latent = pipe.generate_avc(
                video=current, video_latent=latent, prompt=prompt, negative_prompt=NEG, height=height, width=width,
                num_frames=NUM_FRAMES, num_cond_frames=NUM_COND, num_inference_steps=8, text_guidance_scale=1.0,
                audio_guidance_scale=1.0, generator=gen, output_type="both", use_kv_cache=True,
                offload_kv_cache=LOW_VRAM, enhance_hf=False, audio_emb=window(start), ref_latent=ref_latent,
                ref_img_index=10, mask_frame_range=3, use_distill=True,
            )
            current = to_frames(output[0])
            frames.extend(current[NUM_COND:])
        report(0.97, "编码视频")
        # 视频按补齐后的音频长度生成，裁到原始音频时长
        frames = frames[: max(1, math.ceil(seconds * FPS))]
        out = job["out"]
        save_video_ffmpeg(torch.from_numpy(np.array(frames)), out[:-4] if out.endswith(".mp4") else out,
                          job["audio"], fps=FPS, quality=5)

    serve(handle)


if __name__ == "__main__":
    print(json.dumps({"engine": "longcat", "low_vram": LOW_VRAM}), flush=True)
    main()
