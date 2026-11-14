"""生成图自动校色：把生成图里「属于商品」的颜色对齐到参考图（如丝巾平铺图）的色板。

只统计并修正「有彩度、且接近参考图某个主色」的像素，灰背景、驼色衣物、皮肤等基本不动；
对齐两项：彩色像素的平均饱和度（彩度缩放）与冷暖（a*/b* 平移），限幅后迭代两轮收敛。
生成图里几乎找不到参考色（商品不在画面里）时不做修改。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# sRGB(D65) ↔ XYZ
_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]])
_MI = np.linalg.inv(_M)
_WP = np.array([0.95047, 1.0, 1.08883])

CHROMA_MIN = 12.0      # 彩度低于此视为中性色，不参与统计
NEAR_DE = 30.0         # 与参考主色的色差小于此才算「商品颜色」（褶皱明暗会拉开色差，放宽）
MIN_SHARE = 0.05       # 商品颜色像素占比低于此则不校色（商品不在画面里）
K_RANGE = (0.85, 1.35)  # 彩度缩放限幅
SHIFT_MAX = 6.0        # a*/b* 平移限幅


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """RGB(0-255) → Lab。"""
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = np.einsum("...j,ij->...i", lin, _M) / _WP
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def to_rgb(lab: np.ndarray) -> np.ndarray:
    """Lab → RGB(0-255)，越界裁剪。"""
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * _WP
    lin = np.einsum("...j,ij->...i", xyz, _MI)
    c = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.clip(lin, 0, None) ** (1 / 2.4) - 0.055)
    return np.clip(c * 255, 0, 255)


def _smoothstep(x: np.ndarray, a: float, b: float) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def _load_rgb(path: Path, max_edge: int | None = None) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max_edge:
            im.thumbnail((max_edge, max_edge))
        return np.asarray(im, dtype=float)


def reference_palette(ref_rgb: np.ndarray, k: int = 12) -> tuple[np.ndarray, float, float, float]:
    """参考图的彩色主色（Lab）与彩色像素的平均彩度、a*、b*。"""
    lab = to_lab(ref_rgb).reshape(-1, 3)
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    colorful = lab[chroma > CHROMA_MIN]
    if len(colorful) < 50:
        raise ValueError("参考图几乎没有彩色，无法作为色板")
    img = Image.fromarray(ref_rgb.astype(np.uint8)).quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = np.array(img.getpalette()[: k * 3], dtype=float).reshape(-1, 3)
    pal_lab = to_lab(pal)
    pal_lab = pal_lab[np.hypot(pal_lab[:, 1], pal_lab[:, 2]) > CHROMA_MIN] if len(pal_lab) else pal_lab
    if not len(pal_lab):
        pal_lab = colorful[:: max(1, len(colorful) // k)][:k]
    c = np.hypot(colorful[:, 1], colorful[:, 2])
    return pal_lab, float(c.mean()), float(colorful[:, 1].mean()), float(colorful[:, 2].mean())


# 肤色 / 驼色 / 米色保护区：色相角 42°–85°、彩度 8–40（黄橙低彩），默认不参与统计与修正
SKIN_HUE = (42.0, 85.0)
SKIN_CHROMA = (8.0, 40.0)


def _in_skin_zone(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """是否落在肤色/驼色保护区（布尔数组）。"""
    hue = np.degrees(np.arctan2(b, a)) % 360
    chroma = np.hypot(a, b)
    return (hue >= SKIN_HUE[0]) & (hue <= SKIN_HUE[1]) & (chroma >= SKIN_CHROMA[0]) & (chroma <= SKIN_CHROMA[1])


def _product_weight(lab: np.ndarray, pal_lab: np.ndarray, protect_skin: bool = True) -> np.ndarray:
    """像素属于「商品颜色」的权重：有彩度 × 接近参考主色 ×（不在肤色/驼色保护区）。"""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    # 色差只看色度（a*、b*），忽略亮度：褶皱明暗不该让同一块颜色被判为非商品
    d = np.min(np.linalg.norm(lab[..., None, 1:] - pal_lab[None, None, :, 1:], axis=-1), axis=-1)
    w = _smoothstep(chroma, 8, 22) * (1 - _smoothstep(d, NEAR_DE * 0.6, NEAR_DE * 1.2))
    if protect_skin:
        w = np.where(_in_skin_zone(lab[..., 1], lab[..., 2]), 0.0, w)
    return w


def match_colors_array(gen_rgb: np.ndarray, ref_rgb: np.ndarray, *, rounds: int = 2) -> tuple[np.ndarray, dict]:
    """对齐生成图的商品颜色到参考色板，返回 (新 RGB 数组, 统计)。商品颜色太少时原样返回。"""
    pal_lab, ref_c, ref_a, ref_b = reference_palette(ref_rgb)
    # 参考色板本身就有肤色/驼色系颜色时，不启用保护区（否则这些商品颜色永远校不到）
    protect_skin = not bool(_in_skin_zone(pal_lab[:, 1], pal_lab[:, 2]).any())
    lab = to_lab(gen_rgb)
    stats: dict = {"ref_chroma": round(ref_c, 1), "ref_a": round(ref_a, 1), "ref_b": round(ref_b, 1),
                   "applied": False, "protect_skin": protect_skin}
    for i in range(rounds):
        w = _product_weight(lab, pal_lab, protect_skin)
        mask = w > 0.3
        share = float(mask.mean())
        if share < MIN_SHARE:
            stats["share"] = round(share, 3)
            return to_rgb(lab) if i else gen_rgb, stats
        sel = lab[mask]
        c = np.hypot(sel[:, 1], sel[:, 2])
        k = float(np.clip(ref_c / max(c.mean(), 1e-6), *K_RANGE))
        da = float(np.clip(ref_a - sel[:, 1].mean(), -SHIFT_MAX, SHIFT_MAX))
        db = float(np.clip(ref_b - sel[:, 2].mean(), -SHIFT_MAX, SHIFT_MAX))
        if i == 0:
            stats.update(share=round(share, 3), before_chroma=round(float(c.mean()), 1),
                         before_a=round(float(sel[:, 1].mean()), 1), before_b=round(float(sel[:, 2].mean()), 1))
        ww = w[..., None]
        lab = np.concatenate([lab[..., :1], lab[..., 1:] * (1 + (k - 1) * ww) + np.array([da, db]) * ww], -1)
    stats["applied"] = True
    return to_rgb(lab), stats


def match_colors_file(gen_path: Path, ref_path: Path, out_path: Path) -> dict:
    """文件版：校色后写 out_path（.jpg 存高质量 JPEG，其余存 PNG）。返回统计；未校色时不写文件，stats['applied']=False。"""
    gen = _load_rgb(gen_path)
    ref = _load_rgb(ref_path, max_edge=800)
    new, stats = match_colors_array(gen, ref)
    if stats.get("applied"):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(np.round(new).astype(np.uint8))
        if out_path.suffix.lower() in {".jpg", ".jpeg"}:
            img.save(out_path, format="JPEG", quality=95, subsampling=0)
        else:
            img.save(out_path, format="PNG", optimize=True)
    return stats
