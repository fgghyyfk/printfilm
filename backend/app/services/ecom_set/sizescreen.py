"""日化/标品「尺寸标注」信息屏：商品抠图 + 高、宽标注线 + 厚度与常见物件参照。

不调 AI，按商品库填的实际尺寸（厘米）排版；没有尺寸时这张自动跳过。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.ecom_set import dims as dims_mod
from app.services.ecom_set import typo

LINE = (150, 138, 124)


def _cutout(path: str) -> Image.Image | None:
    """读取商品抠图（非透明图按白底抠），裁掉透明边。"""
    from app.services.ecom_set.composite import flatten_white, has_alpha, load_cutout

    p = Path(path or "")
    if not p.exists():
        return None
    cut = load_cutout(p) if has_alpha(p) else flatten_white(p).convert("RGBA")
    box = cut.getbbox()
    return cut.crop(box) if box else cut


def _arrow_line(d: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], width: int, tick: float) -> None:
    """两端带短横档的标注线（a→b 水平或竖直）。"""
    d.line([a, b], fill=LINE, width=width)
    if a[0] == b[0]:  # 竖线：两端横档
        for y in (a[1], b[1]):
            d.line([(a[0] - tick, y), (a[0] + tick, y)], fill=LINE, width=width)
    else:
        for x in (a[0], b[0]):
            d.line([(x, a[1] - tick), (x, a[1] + tick)], fill=LINE, width=width)


def _label(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, f, *, anchor_center: bool = True) -> None:
    """标注文字（水平居中于 xy）。"""
    w = typo.text_width(text, f, 0.04)
    x = xy[0] - w / 2 if anchor_center else xy[0]
    typo.draw_text(d, (x, xy[1]), text, f, typo.INK, 0.04)


def draw_size(img: Image.Image, y: int, bottom: int, dims: dict, product: str, style: typo.TypeStyle) -> None:
    """在 [y, bottom] 区域画商品与尺寸标注。"""
    W, _ = img.size
    d = ImageDraw.Draw(img)
    size = dims_mod.normalize(dims)
    f = typo.font("serif" if style.id == "serif" else "sans", False, int(W * 0.034))
    small = typo.font("sans", False, int(W * 0.026))
    width, tick = max(2, W // 400), W * 0.012
    # 底部说明：厚度 + 常见物件参照
    notes = []
    if "d" in size:
        notes.append(f"厚 {size['d']:g} cm")
    near = dims_mod.reference(size)
    if near:
        notes.append(near)
    note_h = typo.line_h(small, 0.8) if notes else 0
    area_top, area_bottom = y + int(W * 0.03), bottom - note_h - int(W * 0.17)
    cut = _cutout(product)
    box_w, box_h = W * 0.5, max(1, area_bottom - area_top)
    if cut is not None:
        scale = min(box_w / cut.width, box_h / cut.height)
        cw, ch = max(1, int(cut.width * scale)), max(1, int(cut.height * scale))
        cut = cut.resize((cw, ch), Image.Resampling.LANCZOS)
    else:
        cw, ch = int(box_w * 0.5), int(box_h)
    x0 = int((W - cw) / 2 - W * 0.04)
    y0 = area_top + (area_bottom - area_top - ch) // 2
    if cut is not None:
        img.alpha_composite(cut, (x0, y0))
    gap = W * 0.05
    if "h" in size:
        lx = x0 + cw + gap
        _arrow_line(d, (lx, y0), (lx, y0 + ch), width, tick)
        text = f"高 {size['h']:g} cm"
        _label(d, (lx + W * 0.03, y0 + ch / 2 - f.size / 2), text, f, anchor_center=False)
    if "w" in size:
        ly = y0 + ch + gap * 0.8
        _arrow_line(d, (x0, ly), (x0 + cw, ly), width, tick)
        _label(d, (x0 + cw / 2, ly + W * 0.02), f"宽 {size['w']:g} cm", f)
    if notes:
        _label(d, (W / 2, bottom - note_h), " ｜ ".join(notes), small)
