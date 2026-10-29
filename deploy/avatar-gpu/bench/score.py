"""数字人 P0 实测：给 results/ 下各用例输出打客观分，生成报告与并排对比视频。

指标：
- SyncNet 置信度 / 音画偏移：LatentSync 自带评测（越高越同步；偏移接近 0 为佳）
- 人脸一致性：ArcFace 余弦相似度（参考脸 vs 每秒抽帧），看均值与最低值（最低值反映中途「变脸」）
- 偏色：前 5 秒与后 5 秒平均 Lab 色差 ΔE（InfiniteTalk 官方提示长视频会偏色）
- 速度/成本：耗时 ÷ 视频时长（实时倍数）、每分钟成片 GPU 分钟与费用

需在 latentsync conda 环境运行（复用其 insightface / onnxruntime-gpu / SyncNet），由 score.sh 调用。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def probe(path: Path) -> dict:
    """读取视频宽高、帧率、时长。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    st = data["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {
        "width": st["width"], "height": st["height"],
        "fps": round(float(num) / float(den), 2),
        "duration": round(float(data["format"]["duration"]), 2),
    }


def sample_frames(path: Path, times: list[float]) -> list[np.ndarray]:
    """按时间点抽帧（BGR）。"""
    cap = cv2.VideoCapture(str(path))
    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    return frames


def load_face_app():
    """初始化 insightface（buffalo_l：检测 + ArcFace 识别）。"""
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


def face_embedding(app, img: np.ndarray) -> np.ndarray | None:
    """取画面中最大人脸的归一化特征向量；无人脸返回 None。"""
    faces = app.get(img)
    if not faces:
        return None
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return face.normed_embedding


def identity_scores(app, ref_img: np.ndarray, video: Path, duration: float) -> dict:
    """参考脸与每秒抽帧的相似度统计。"""
    ref = face_embedding(app, ref_img)
    if ref is None:
        return {"error": "参考图未检测到人脸"}
    times = [t + 0.5 for t in range(int(duration))]
    sims = []
    for frame in sample_frames(video, times):
        emb = face_embedding(app, frame)
        sims.append(float(np.dot(ref, emb)) if emb is not None else None)
    valid = [s for s in sims if s is not None]
    if not valid:
        return {"error": "视频中未检测到人脸"}
    return {
        "mean": round(float(np.mean(valid)), 3),
        "min": round(float(np.min(valid)), 3),
        "p10": round(float(np.percentile(valid, 10)), 3),
        "face_rate": round(len(valid) / max(1, len(sims)), 3),
    }


def color_drift(video: Path, duration: float) -> float | None:
    """前 5 秒与后 5 秒平均 Lab 颜色的欧氏距离 ΔE。"""
    if duration < 12:
        return None
    head = sample_frames(video, [i * 0.5 for i in range(10)])
    tail = sample_frames(video, [duration - 5 + i * 0.5 for i in range(10)])
    if not head or not tail:
        return None

    def mean_lab(frames: list[np.ndarray]) -> np.ndarray:
        """多帧平均 Lab（OpenCV 8 位 Lab 换算回标准尺度）。"""
        labs = [cv2.cvtColor(f, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(axis=0) for f in frames]
        m = np.mean(labs, axis=0)
        return np.array([m[0] * 100 / 255, m[1] - 128, m[2] - 128])

    return round(float(np.linalg.norm(mean_lab(head) - mean_lab(tail))), 2)


def syncnet_score(video: Path, latentsync_dir: Path) -> dict:
    """调用 LatentSync 的 SyncNet 评测，解析置信度与音画偏移。"""
    proc = subprocess.run(
        [sys.executable, "-m", "eval.eval_sync_conf", "--video_path", str(video),
         "--initial_model", "checkpoints/auxiliary/syncnet_v2.model"],
        cwd=latentsync_dir, capture_output=True, text=True,
    )
    text = proc.stdout + proc.stderr
    conf = re.search(r"SyncNet confidence:\s*([-\d.]+)", text)
    off = re.search(r"AV offset:\s*(-?\d+)", text)
    if not conf:
        return {"error": text.strip().splitlines()[-1][:200] if text.strip() else "无输出"}
    return {"confidence": float(conf.group(1)), "av_offset": int(off.group(1)) if off else None}


def reference_image(case: str, inputs: Path) -> np.ndarray | None:
    """i2v / Seedance 用半身照当参考脸；v2v 用底片首帧。"""
    if "v2v" in case:
        frames = sample_frames(inputs / "base.mp4", [0.5])
        return frames[0] if frames else None
    img = cv2.imread(str(inputs / "portrait.png"))
    return img


def make_grid(items: list[tuple[str, Path]], audio: Path, dest: Path, cell=(360, 640)) -> None:
    """把各用例视频缩放到同尺寸、加标签后拼成宫格，配统一口播音轨。"""
    w, h = cell
    n = len(items)
    cols = math.ceil(math.sqrt(n))
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for _, path in items:
        cmd += ["-i", str(path)]
    cmd += ["-i", str(audio)]
    chains = []
    for i, (label, _) in enumerate(items):
        f = (f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25")
        if FONT.exists():
            f += (f",drawtext=fontfile={FONT}:text='{label}':x=8:y=8:fontsize=18:"
                  "fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=4")
        chains.append(f + f"[v{i}]")
    if n == 1:
        graph = ";".join(chains) + ";[v0]null[out]"
    else:
        layout = "|".join(f"{(i % cols) * w}_{(i // cols) * h}" for i in range(n))
        ins = "".join(f"[v{i}]" for i in range(n))
        graph = ";".join(chains) + f";{ins}xstack=inputs={n}:layout={layout}:fill=black[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-map", f"{n}:a",
            "-shortest", "-c:v", "libx264", "-crf", "23", "-c:a", "aac", str(dest)]
    subprocess.run(cmd, check=True)


def fmt(v, suffix: str = "") -> str:
    """表格单元格格式化，缺失显示 -。"""
    return "-" if v is None else f"{v}{suffix}"


def write_report(rows: list[dict], dest: Path, price: float | None) -> None:
    """输出 Markdown 对比表 + 主观打分模板。"""
    lines = [
        "# 数字人 P0 实测报告", "",
        "| 用例 | 分辨率 | 时长 | 耗时 | 实时倍数 | 显存峰值 | SyncNet↑ | 偏移 | 人脸均值↑ | 人脸最低↑ | 偏色ΔE↓ | 每分钟成本 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        meta, vid, ids, sync = r["meta"], r["video"], r["identity"], r["sync"]
        wall = meta.get("wall_seconds")
        rtf = round(wall / vid["duration"], 1) if wall and vid["duration"] else None
        vram = ", ".join(f"{k}:{v // 1024}G" for k, v in (meta.get("peak_vram_mib") or {}).items())
        cost = None
        if price and wall and vid["duration"]:
            cost = round(wall * meta.get("ngpu", 1) / 3600 * price / (vid["duration"] / 60), 2)
        lines.append(
            f"| {r['case']} | {vid['width']}x{vid['height']} | {vid['duration']}s | {fmt(wall, 's')} | "
            f"{fmt(rtf, 'x')} | {vram or '-'} | {fmt(sync.get('confidence'))} | {fmt(sync.get('av_offset'))} | "
            f"{fmt(ids.get('mean'))} | {fmt(ids.get('min'))} | {fmt(r['color_drift'])} | {fmt(cost, ' 元')} |"
        )
    lines += [
        "", "说明：SyncNet 置信度一般 >6 为好、>8 很好；人脸相似度 >0.6 基本是同一人，最低值骤降说明中途变脸；",
        "偏色 ΔE >5 肉眼可见。实时倍数 = 耗时 ÷ 成片时长。Seedance 为云端 API，耗时含排队，成本看计费记录。", "",
        "## 主观打分（1–5，看 compare_grid.mp4 后填写）", "",
        "| 用例 | 口型准确 | 表情/动作自然 | 人脸稳定 | 画质清晰 | 能否直接用于口播成片 |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['case']} |  |  |  |  |  |" for r in rows]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """遍历 results/*/output.mp4，逐个打分并汇总。"""
    p = argparse.ArgumentParser(description="数字人实测评分")
    p.add_argument("--results", default=str(BENCH_DIR / "results"))
    p.add_argument("--latentsync-dir", required=True)
    p.add_argument("--gpu-price", type=float, default=None, help="每卡每小时价格（元），用于估算每分钟成本")
    p.add_argument("--skip-sync", action="store_true", help="跳过 SyncNet（调试用）")
    args = p.parse_args()

    results = Path(args.results)
    inputs = results / "_inputs"
    if not (inputs / "portrait.png").exists() and (BENCH_DIR / "inputs/portrait.png").exists():
        (inputs / "portrait.png").write_bytes((BENCH_DIR / "inputs/portrait.png").read_bytes())
    app = load_face_app()
    rows = []
    for case_dir in sorted(d for d in results.iterdir() if d.is_dir() and not d.name.startswith("_")):
        video = case_dir / "output.mp4"
        if not video.exists():
            print(f"跳过 {case_dir.name}：无 output.mp4")
            continue
        print(f"评分 {case_dir.name} …")
        meta_path = case_dir / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        info = probe(video)
        ref = reference_image(case_dir.name, inputs)
        row = {
            "case": case_dir.name, "meta": meta, "video": info,
            "identity": identity_scores(app, ref, video, info["duration"]) if ref is not None else {"error": "无参考图"},
            "color_drift": color_drift(video, info["duration"]),
            "sync": {} if args.skip_sync else syncnet_score(video, Path(args.latentsync_dir)),
        }
        print(json.dumps(row, ensure_ascii=False))
        rows.append(row)

    if not rows:
        sys.exit("results/ 下没有可评分的 output.mp4")
    (results / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    write_report(rows, results / "report.md", args.gpu_price)
    make_grid([(r["case"], results / r["case"] / "output.mp4") for r in rows],
              inputs / "audio.wav", results / "compare_grid.mp4")
    print(f"完成：{results / 'report.md'}，{results / 'compare_grid.mp4'}")


if __name__ == "__main__":
    main()
