"""数字人 P0 实测：在本地（Mac）准备统一测试素材，产出 inputs/ 下的标准文件。

产出：
- audio.wav       口播音频（44.1kHz 单声道，响度 -16 LUFS），所有模型共用
- voice_ref.wav   前 10 秒音色样本，给 Seedance 当 reference_audio
- portrait.png    半身照（照片数字人用）
- base.mp4        可选，真人口播底片（视频→视频配音用，25fps，长度补齐到音频）
- manifest.json   时长等元信息

用法（在 backend/ 下用项目虚拟环境，已带 edge-tts）：
  .venv/bin/python ../deploy/avatar-gpu/bench/prepare_inputs.py --portrait ~/Desktop/me.jpg
  .venv/bin/python ../deploy/avatar-gpu/bench/prepare_inputs.py --audio ~/rec.m4a --base-video ~/base.mov
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INPUT_DIR = Path(__file__).resolve().parent / "inputs"


def run(cmd: list[str]) -> None:
    """执行外部命令，失败时打印命令并退出。"""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("命令失败：", " ".join(cmd), "\n", proc.stderr[-2000:], file=sys.stderr)
        sys.exit(1)


def media_seconds(path: Path) -> float:
    """用 ffprobe 读取媒体时长（秒）。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


async def tts_edge(text: str, voice: str, rate: str, dest: Path) -> None:
    """用 edge-tts 合成口播（mp3）。"""
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(str(dest))


def tts_fallback_say(text: str, dest: Path) -> None:
    """edge-tts 不可用时用 macOS 自带婷婷音色兜底（音质一般，只用于跑通流程）。"""
    if not shutil.which("say"):
        sys.exit("edge-tts 不可用且本机无 say 命令：请用 --audio 传入自备口播音频")
    print("警告：edge-tts 失败，改用 macOS say（Tingting）合成；正式对比建议用 --audio 传真人录音或项目 TTS 成品")
    run(["say", "-v", "Tingting", "-r", "170", "-o", str(dest), text])  # 约 4.3 字/秒，接近真人口播


def build_audio(args: argparse.Namespace, out: Path) -> str:
    """生成或转换口播音频，统一采样率、声道与响度；返回实际音频来源说明。"""
    source = f"file:{args.audio}" if args.audio else f"edge-tts:{args.voice}"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(args.audio).expanduser() if args.audio else Path(tmp) / "tts.mp3"
        if not args.audio:
            text = (INPUT_DIR / "script.txt").read_text(encoding="utf-8").strip()
            print(f"edge-tts 合成口播：{len(text)} 字，音色 {args.voice}")
            try:
                asyncio.run(tts_edge(text, args.voice, args.rate, src))
            except Exception as exc:  # noqa: BLE001 - 任何网络/协议错误都走兜底
                print(f"edge-tts 失败：{type(exc).__name__}")
                src = Path(tmp) / "tts.aiff"
                tts_fallback_say(text, src)
                source = "macos-say:Tingting"
        run([
            "ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "44100",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(out),
        ])
    return source


def build_portrait(src: str, out: Path) -> None:
    """把半身照转成 PNG（保留原始比例，模型按自身分桶缩放）。"""
    run(["ffmpeg", "-y", "-i", str(Path(src).expanduser()), "-frames:v", "1", str(out)])


def build_base_video(src: str, audio_sec: float, out: Path) -> None:
    """底片转 25fps H.264，并循环补齐到音频长度（底片本身越接近音频长度越好）。"""
    src_path = Path(src).expanduser()
    src_sec = media_seconds(src_path)
    if src_sec < audio_sec:
        print(f"提示：底片 {src_sec:.1f}s 短于音频 {audio_sec:.1f}s，将循环补齐，接缝处会跳帧")
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src_path), "-t", f"{audio_sec:.2f}",
        "-an", "-r", "25", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(out),
    ])


def main() -> None:
    """解析参数并依次生成测试素材与 manifest。"""
    p = argparse.ArgumentParser(description="准备数字人实测素材")
    p.add_argument("--audio", help="自备口播音频（任意格式）；不传则用 script.txt + edge-tts 合成")
    p.add_argument("--voice", default="zh-CN-XiaoxiaoNeural", help="edge-tts 音色")
    p.add_argument("--rate", default="+5%", help="edge-tts 语速")
    p.add_argument("--portrait", help="半身照（jpg/png），不传则沿用 inputs/portrait.png")
    p.add_argument("--base-video", help="可选：真人口播底片，用于视频→视频配音对比")
    args = p.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"缺少 {tool}，请先安装")

    audio = INPUT_DIR / "audio.wav"
    audio_source = build_audio(args, audio)
    audio_sec = media_seconds(audio)
    print(f"audio.wav：{audio_sec:.1f}s")

    voice_ref = INPUT_DIR / "voice_ref.wav"
    run(["ffmpeg", "-y", "-i", str(audio), "-t", "10", str(voice_ref)])

    portrait = INPUT_DIR / "portrait.png"
    if args.portrait:
        build_portrait(args.portrait, portrait)
    if not portrait.exists():
        print("警告：缺少 inputs/portrait.png，照片数字人用例将无法运行")

    base = INPUT_DIR / "base.mp4"
    if args.base_video:
        build_base_video(args.base_video, audio_sec, base)

    manifest = {
        "audio_sec": round(audio_sec, 2),
        "has_portrait": portrait.exists(),
        "has_base_video": base.exists(),
        "audio_source": audio_source,
    }
    (INPUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("manifest.json：", manifest)


if __name__ == "__main__":
    main()
