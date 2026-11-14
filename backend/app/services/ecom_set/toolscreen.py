"""电动工具「细节特写」：从商品抠图里裁出机头、铭牌、握把、电池等局部放大，2×2 宫格排版。

不调 AI：细节全部来自实拍商品图，铭牌文字、logo 不会被重画；免费、秒出。
手枪式工具按「机头一侧在上、握把与电池在下」的结构取局部，其余形态按四象限取。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from app.services.ecom_set import typo

SLOT_KEY = "tool_detail"
# 手枪式局部：名称 + 相对商品外框的裁切区域（机头朝右时；朝左时水平镜像坐标）
PISTOL_PARTS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("机头与夹头", (0.58, 0.0, 1.0, 0.34)),
    ("机身铭牌", (0.04, 0.0, 0.62, 0.32)),
    ("防滑握把", (0.12, 0.26, 0.66, 0.74)),
    ("锂电池包", (0.0, 0.70, 0.82, 1.0)),
)
QUADRANTS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("", (0.0, 0.0, 0.55, 0.55)), ("", (0.45, 0.0, 1.0, 0.55)),
    ("", (0.0, 0.45, 0.55, 1.0)), ("", (0.45, 0.45, 1.0, 1.0)),
)
CARD = (255, 255, 255)
CARD_LINE = (226, 220, 212)


def _load(path: str) -> Image.Image | None:
    """读商品图为 RGBA（透明底保留，白底图按白底处理），裁到商品外框。"""
    from app.services.ecom_set.composite import flatten_white, has_alpha, load_cutout

    p = Path(path or "")
    if not p.exists():
        return None
    im = load_cutout(p) if has_alpha(p) else flatten_white(p).convert("RGBA")
    box = im.getchannel("A").getbbox()
    return im.crop(box) if box else im


def head_on_right(cut: Image.Image) -> bool:
    """手枪式工具的机头是否朝右：机头一侧上部有实体、下部是空的（握把在另一侧）。"""
    a = np.asarray(cut.getchannel("A"), dtype=np.float32) / 255
    h, w = a.shape
    top, bottom = a[: int(h * 0.3)], a[int(h * 0.45): int(h * 0.7)]
    third = max(1, w // 3)
    left = top[:, :third].mean() - bottom[:, :third].mean()
    right = top[:, -third:].mean() - bottom[:, -third:].mean()
    return right >= left


def parts_for(cut: Image.Image, form: str) -> list[tuple[str, tuple[int, int, int, int]]]:
    """按形态给出各局部的像素裁切框。"""
    w, h = cut.size
    if form == "pistol":
        mirror = not head_on_right(cut)
        regions = [(n, (1 - x1, y0, 1 - x0, y1) if mirror else (x0, y0, x1, y1))
                   for n, (x0, y0, x1, y1) in PISTOL_PARTS]
    else:
        regions = list(QUADRANTS)
    return [(n, (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))) for n, (x0, y0, x1, y1) in regions]


def draw_details(img: Image.Image, y: int, bottom: int, product: str, form: str) -> None:
    """在 y～bottom 区域画 2×2 局部宫格；每格白卡片 + 局部放大 + 下方名称。"""
    cut = _load(product)
    if cut is None:
        return
    W = img.size[0]
    d = ImageDraw.Draw(img)
    margin, gap = int(W * 0.07), int(W * 0.035)
    cell_w = (W - margin * 2 - gap) // 2
    cell_h = (bottom - y - gap) // 2
    cap_f = typo.font("sans", True, int(W * 0.03))
    cap_h = typo.line_h(cap_f, 0.9)
    radius = int(W * 0.02)
    for i, (name, box) in enumerate(parts_for(cut, form)):
        cx = margin + (i % 2) * (cell_w + gap)
        cy = y + (i // 2) * (cell_h + gap)
        d.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h), radius=radius, fill=CARD, outline=CARD_LINE,
                            width=max(1, W // 720))
        pic_h = cell_h - (cap_h if name else 0) - int(W * 0.02)
        pad = int(W * 0.02)
        part = cut.crop(box)
        part = ImageOps.contain(part, (cell_w - pad * 2, pic_h - pad), Image.Resampling.LANCZOS)
        img.alpha_composite(part, (cx + (cell_w - part.width) // 2, cy + pad + (pic_h - pad - part.height) // 2))
        if name:
            tw = typo.text_width(name, cap_f, 0.06)
            typo.draw_text(d, (cx + (cell_w - tw) / 2, cy + cell_h - cap_h - int(W * 0.012)), name, cap_f, typo.INK, 0.06)
