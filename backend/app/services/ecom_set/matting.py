"""商品实拍图本地抠图：BiRefNet 轻量版（ONNX，CPU 推理，不走上游、不计费）。

用户上传自己拍的商品照片（不是透明底）时，自动抠出商品主体存成透明 PNG：
白底图直接铺白，场景合成也用这张抠图贴回，商品像素始终是实拍的。
模型文件不进仓库，由配置 ECOM_MATTING_MODEL 指定路径；文件不存在时抠图功能整体关闭（上传照常）。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

INPUT = 1024                       # 模型固定输入边长
MAX_SIDE = 2400                    # 输出抠图最长边（原图更大时先缩，够详情页用）
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
LOW, HIGH = 0.04, 0.96             # 遮罩两端收紧：去掉背景残影、商品内部不发虚
MIN_COVER, MAX_COVER = 0.01, 0.97  # 主体面积占比合理范围，超出视为没找到商品
PAD = 0.02                         # 裁切时四周留一点边

_lock = threading.Lock()
_session = None


class MattingError(RuntimeError):
    """抠图失败（模型不可用或没找到商品主体），文案可直接给用户看。"""


def model_path() -> Path:
    """模型文件路径（配置优先，默认 backend/models/birefnet-lite.onnx）。"""
    from app.config import get_settings

    raw = str(getattr(get_settings(), "ecom_matting_model", "") or "")
    return Path(raw) if raw else Path(__file__).resolve().parents[3] / "models" / "birefnet-lite.onnx"


def available() -> bool:
    """模型文件在、onnxruntime 装了，才开启自动抠图。"""
    if not model_path().is_file():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _get_session():
    """懒加载推理会话（进程内只建一次；限制线程数，避免占满共享服务器的 CPU）。"""
    global _session
    if _session is None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        opts.inter_op_num_threads = 1
        _session = ort.InferenceSession(str(model_path()), sess_options=opts, providers=["CPUExecutionProvider"])
    return _session


def predict_mask(img: Image.Image) -> Image.Image:
    """跑模型得到与原图同尺寸的灰度遮罩（L，255 = 商品）。"""
    x = np.asarray(img.convert("RGB").resize((INPUT, INPUT), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    with _lock:  # 同一时间只跑一张，控制内存峰值
        sess = _get_session()
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    m = 1.0 / (1.0 + np.exp(-out))
    m = (m - m.min()) / max(1e-6, float(m.max() - m.min()))
    small = Image.fromarray((m * 255).astype(np.uint8), "L")
    return small.resize(img.size, Image.Resampling.BILINEAR)


def refine(mask: Image.Image) -> Image.Image:
    """遮罩收紧两端：背景残影归零、商品内部填实，只保留边缘过渡。"""
    a = np.asarray(mask, dtype=np.float32) / 255.0
    a = np.clip((a - LOW) / (HIGH - LOW), 0.0, 1.0)
    return Image.fromarray((a * 255).astype(np.uint8), "L")


def _components(binary: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """四邻域连通块标号（小图上跑，纯 numpy + 栈），返回 (标号图, 每块面积)，标号从 1 开始。"""
    h, w = binary.shape
    labels = np.zeros((h, w), dtype=np.int32)
    sizes: list[int] = []
    flat = binary.ravel()
    lab = labels.ravel()
    for start in np.flatnonzero(flat):
        if lab[start]:
            continue
        n = len(sizes) + 1
        lab[start] = n
        stack, count = [int(start)], 0
        while stack:
            i = stack.pop()
            count += 1
            y, x = divmod(i, w)
            for j in (i - w if y else -1, i + w if y < h - 1 else -1, i - 1 if x else -1, i + 1 if x < w - 1 else -1):
                if j >= 0 and flat[j] and not lab[j]:
                    lab[j] = n
                    stack.append(j)
        sizes.append(count)
    return labels, sizes


KEEP_RATIO = 0.6  # 明显小于最大块的（画面文字、旁边的橙子片等道具）去掉；两支同款并排大小相近会都保留


def keep_main(mask: Image.Image) -> Image.Image:
    """只留商品主体：去掉画面上的文字、远处小道具等零碎块。"""
    small = mask.copy()
    small.thumbnail((256, 256), Image.Resampling.BILINEAR)
    labels, sizes = _components(np.asarray(small) > 127)
    if len(sizes) <= 1:
        return mask
    big = max(sizes)
    keep = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= big * KEEP_RATIO])
    # 边缘半透明过渡在小图上可能断开：保留区放大一圈再乘回原遮罩
    from PIL import ImageFilter

    gate = Image.fromarray((keep * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(5))
    gate = gate.resize(mask.size, Image.Resampling.BILINEAR)
    return Image.fromarray((np.asarray(mask, dtype=np.uint16) * np.asarray(gate) // 255).astype(np.uint8), "L")


def apply_mask(img: Image.Image, mask: Image.Image) -> Image.Image:
    """按遮罩抠出主体并裁到商品范围（四周留 2%），返回 RGBA。"""
    a = np.asarray(mask)
    cover = float((a > 127).mean())
    if not MIN_COVER <= cover <= MAX_COVER:
        raise MattingError("没有识别出商品主体，请换一张商品更清楚、背景更干净的照片")
    rgba = img.convert("RGB").convert("RGBA")
    rgba.putalpha(mask)
    box = Image.fromarray(((a > 8) * 255).astype(np.uint8), "L").getbbox()
    if not box:
        raise MattingError("没有识别出商品主体，请换一张照片")
    pw, ph = int(img.width * PAD), int(img.height * PAD)
    return rgba.crop((max(0, box[0] - pw), max(0, box[1] - ph), min(img.width, box[2] + pw), min(img.height, box[3] + ph)))


def cutout(src: Path) -> Image.Image:
    """读一张商品照片，返回抠好的透明底商品图。"""
    if not available():
        raise MattingError("抠图功能暂未开启")
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return apply_mask(img, keep_main(refine(predict_mask(img))))


def cutout_to(src: Path, dest: Path) -> Path:
    """抠图并存 PNG，返回保存路径。"""
    out = cutout(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG", optimize=True)
    return dest
