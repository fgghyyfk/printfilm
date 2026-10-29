"""数字人 P0 实测：Seedance 基线（本地运行，走项目开放 API /api/v1/seedance/tasks）。

做法：口播稿按句切成 ≤12 秒的段，每段提交「参考图（半身照）+ 参考音频（音色）+ 逐字台词」，
开启原生配音；全部完成后下载并拼接成 results/seedance/output.mp4，写 meta.json 供 score.py 汇总。

前置：
- 本地后端在跑，且已在用户端「API Key」页创建 Key（会正常计费）
- 半身照与音色样本需是公网 https 链接（Seedance 无法拉取 localhost/内网文件），
  可上传到 OSS 或图床；音色样本用 prepare_inputs.py 产出的 inputs/voice_ref.wav

用法（在 backend/ 下）：
  PF_API_KEY=sk-xxx .venv/bin/python ../deploy/avatar-gpu/bench/seedance_baseline.py \
      --image-url https://.../portrait.png --voice-url https://.../voice_ref.wav
  加 --dry-run 只打印分段与请求体，不提交。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BENCH_DIR = Path(__file__).resolve().parent
CHARS_PER_SEC = 4.5  # 与 seedance_segments.py 的口播语速估算一致

PROMPT_TMPL = (
    "参考图1中的人物，{scene}。固定机位中景，人物正对镜头自然口播，表情亲切，带少量手势。"
    "人物音色参考音频1。人物用普通话逐字说出以下台词，不增删改字：「{line}」"
    "画面内禁止出现任何字幕、文字、水印；无背景音乐，只有人声和轻微环境音。"
)


def split_script(text: str, max_sec: float) -> list[str]:
    """按句号/问号/感叹号切句，再贪心合并到每段不超过 max_sec 秒的朗读量。"""
    sentences = [s for s in re.split(r"(?<=[。！？!?])", text.strip()) if s.strip()]
    limit = int(max_sec * CHARS_PER_SEC)
    segs: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) > limit:
            segs.append(cur)
            cur = ""
        cur += s
    if cur:
        segs.append(cur)
    return segs


def seg_duration(line: str) -> int:
    """按语速估时长并留 1 秒余量，钳到 Seedance 允许的 4–30 秒。"""
    return max(4, min(30, math.ceil(len(line) / CHARS_PER_SEC) + 1))


def build_body(line: str, args: argparse.Namespace) -> dict:
    """构造开放 API 的 Seedance 多模态请求体。"""
    return {
        "content": [
            {"type": "text", "text": PROMPT_TMPL.format(scene=args.scene, line=line)},
            {"type": "image_url", "image_url": {"url": args.image_url}, "role": "reference_image"},
            {"type": "audio_url", "audio_url": {"url": args.voice_url}, "role": "reference_audio"},
        ],
        "duration": seg_duration(line),
        "resolution": args.resolution,
        "ratio": args.ratio,
        "generate_audio": True,
        "watermark": False,
    }


def submit_all(client: httpx.Client, bodies: list[dict]) -> list[str]:
    """逐段提交任务，返回上游 task_id 列表。"""
    ids = []
    for i, body in enumerate(bodies, 1):
        resp = client.post("/api/v1/seedance/tasks", json=body)
        if resp.status_code >= 400:
            sys.exit(f"第 {i} 段提交失败 HTTP {resp.status_code}：{resp.text[:300]}")
        ids.append(resp.json()["task_id"])
        print(f"已提交第 {i}/{len(bodies)} 段：{ids[-1]}")
    return ids


def wait_all(client: httpx.Client, ids: list[str], interval: float, timeout: float) -> list[str]:
    """轮询全部任务直到成功，返回各段视频 URL；任一失败即退出。"""
    urls: dict[str, str] = {}
    deadline = time.time() + timeout
    while len(urls) < len(ids):
        if time.time() > deadline:
            sys.exit(f"超时：完成 {len(urls)}/{len(ids)} 段")
        for tid in ids:
            if tid in urls:
                continue
            data = client.get(f"/api/v1/tasks/{tid}").json()
            status = data.get("status")
            if status == "succeeded" and data.get("urls"):
                urls[tid] = data["urls"][0]
                print(f"完成 {len(urls)}/{len(ids)}：{tid}")
            elif status in {"failed", "cancelled", "expired"}:
                sys.exit(f"任务 {tid} 失败：{data.get('error')}")
        if len(urls) < len(ids):
            time.sleep(interval)
    return [urls[t] for t in ids]


def download_and_concat(client: httpx.Client, urls: list[str], dest: Path) -> None:
    """下载各段（相对路径按后端地址补全），统一编码后拼接。"""
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, url in enumerate(urls):
            raw = Path(tmp) / f"raw_{i}.mp4"
            norm = Path(tmp) / f"part_{i}.mp4"
            full = url if url.startswith("http") else str(client.base_url).rstrip("/") + url
            with httpx.stream("GET", full, timeout=300, follow_redirects=True) as r:
                r.raise_for_status()
                with raw.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-r", "25", "-c:v", "libx264",
                 "-crf", "18", "-c:a", "aac", "-ar", "44100", "-ac", "1", str(norm)],
                check=True,
            )
            parts.append(norm)
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(dest)],
            check=True,
        )


def main() -> None:
    """切段 → 提交 → 轮询 → 拼接 → 写 meta.json。"""
    p = argparse.ArgumentParser(description="Seedance 数字人基线")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--api-key", default=os.environ.get("PF_API_KEY", ""))
    p.add_argument("--image-url", default="", help="半身照公网 https 链接")
    p.add_argument("--voice-url", default="", help="音色样本公网 https 链接（2–30 秒）")
    p.add_argument("--script", default=str(BENCH_DIR / "inputs/script.txt"))
    p.add_argument("--scene", default="坐在明亮的高级酒店大堂里")
    p.add_argument("--max-seg-sec", type=float, default=12)
    p.add_argument("--resolution", default="480p")
    p.add_argument("--ratio", default="9:16")
    p.add_argument("--poll-interval", type=float, default=10)
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--out", default=str(BENCH_DIR / "results/seedance"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    segs = split_script(Path(args.script).read_text(encoding="utf-8"), args.max_seg_sec)
    bodies = [build_body(s, args) for s in segs]
    for i, (s, b) in enumerate(zip(segs, bodies), 1):
        print(f"第 {i} 段 {b['duration']}s，{len(s)} 字：{s}")
    if args.dry_run:
        print(json.dumps(bodies[0], ensure_ascii=False, indent=2))
        return
    if not (args.api_key and args.image_url.startswith("https://") and args.voice_url.startswith("https://")):
        sys.exit("需要 --api-key（或 PF_API_KEY）以及 https 的 --image-url / --voice-url")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    headers = {"Authorization": f"Bearer {args.api_key}"}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120) as client:
        ids = submit_all(client, bodies)
        urls = wait_all(client, ids, args.poll_interval, args.timeout)
        download_and_concat(client, urls, out / "output.mp4")
    meta = {
        "case": "seedance", "exit_code": 0, "wall_seconds": int(time.time() - t0), "ngpu": 0,
        "segments": len(segs), "task_ids": ids, "peak_vram_mib": {}, "has_output": True,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"完成：{out / 'output.mp4'}（{meta['wall_seconds']}s）")


if __name__ == "__main__":
    main()
