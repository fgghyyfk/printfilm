"""成图文字可读性把关：每一行字落笔前，按它正下方背景里最不利的部分检查对比度，不够就补救。

所有画在照片上的文字（促销醒目、典雅衬线 / 简约黑体、丝巾竖排标题）都经过这里，
避免「整块区域平均亮度看着够，但字所在那一条正好是中间调 / 亮窗 / 阴影」时字发虚。

补救顺序：字色向同侧的纯黑 / 纯白靠拢 → 仍不够时在这一行背后加一块边缘柔和的局部雾面，逐级加浓。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

# 对比度下限（WCAG：正文 4.5，大字 3）；电商图在手机上缩小看，标题也按 4.5 要求
TITLE_MIN = 4.5
TEXT_MIN = 4.0
DARK_POLE = (20, 18, 16)
LIGHT_POLE = (255, 255, 255)
# 雾面颜色：深字下垫暖白，白字下垫暖黑
LIGHT_VEIL = (250, 247, 242)
DARK_VEIL = (20, 18, 16)
VEIL_STEPS = (0.25, 0.4, 0.55, 0.7, 0.85, 0.95)


def _lum(rgb: tuple[float, ...]) -> float:
    """sRGB 颜色的相对亮度（WCAG）。"""
    def ch(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """两色对比度（1–21）。"""
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_dark(fill: tuple[int, ...]) -> bool:
    """字色是否属于深色一侧。"""
    return _lum(fill) < 0.18


def _clip(img: Image.Image, box: tuple[float, float, float, float]) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    return (x0, y0, x1, y1) if x1 - x0 >= 2 and y1 - y0 >= 2 else None


def worst_bg(img: Image.Image, box: tuple[float, float, float, float], dark_text: bool) -> tuple[int, int, int]:
    """文字框里对这种字色最不利的背景：深字取最暗的 0.5% 分位，白字取最亮的 99.5% 分位（单个字压在一块深色 / 亮斑上也算）。"""
    b = _clip(img, box)
    if b is None:
        return (255, 255, 255) if dark_text else (0, 0, 0)
    region = img.crop(b).convert("RGB")
    region.thumbnail((480, 480))
    gray = region.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    target = total * (0.005 if dark_text else 0.995)
    acc = 0
    level = 0
    for level, n in enumerate(hist):
        acc += n
        if acc >= target:
            break
    # 取接近该分位亮度的像素平均色（保留暖冷色调）
    px = [p for p, g in zip(region.getdata(), gray.getdata(), strict=True) if abs(g - level) <= 6]
    if not px:
        return (level, level, level)
    n = len(px)
    return tuple(round(sum(p[i] for p in px) / n) for i in range(3))  # type: ignore[return-value]


def _mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _veil(img: Image.Image, box: tuple[int, int, int, int], *, light: bool, strength: float, pad: int) -> None:
    """在文字框背后加一块四周渐隐的雾面（不是硬边底板）。"""
    x0, y0, x1, y1 = box
    ox0, oy0 = max(0, x0 - pad * 2), max(0, y0 - pad * 2)
    ox1, oy1 = min(img.width, x1 + pad * 2), min(img.height, y1 + pad * 2)
    mask = Image.new("L", (ox1 - ox0, oy1 - oy0), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (x0 - pad - ox0, y0 - pad - oy0, x1 + pad - ox0, y1 + pad - oy0), radius=pad, fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, pad * 0.6)))
    layer = Image.new("RGBA", mask.size, (*(LIGHT_VEIL if light else DARK_VEIL), 255))
    layer.putalpha(mask)
    img.alpha_composite(layer, (ox0, oy0))


def ensure(img: Image.Image, box: tuple[float, float, float, float], fill: tuple[int, ...], *,
           need: float = TEXT_MIN) -> tuple[int, int, int]:
    """保证这一行字（box 为它占的框）对背景的对比度不低于 need，返回最终字色；必要时就地给 img 加局部雾面。

    字色深浅侧不变（同一块文字不会一行黑一行白），只在同侧加深 / 提亮。img 必须是 RGBA。
    """
    rgb = tuple(int(c) for c in fill[:3])
    dark = is_dark(rgb)
    pole = DARK_POLE if dark else LIGHT_POLE
    bg = worst_bg(img, box, dark)
    if contrast(rgb, bg) >= need:
        return rgb  # type: ignore[return-value]
    for t in (0.25, 0.5, 0.75, 1.0):
        cand = _mix(rgb, pole, t)
        if contrast(cand, bg) >= need:
            return cand
    b = _clip(img, box)
    if b is None:
        return pole
    pad = max(6, int((b[3] - b[1]) * 0.45))
    for strength in VEIL_STEPS:
        _veil(img, b, light=dark, strength=strength, pad=pad)
        if contrast(pole, worst_bg(img, box, dark)) >= need:
            break
    return pole


def short(img: Image.Image, box: tuple[float, float, float, float], fill: tuple[int, ...], *,
          need: float = TEXT_MIN) -> bool:
    """只靠同侧加深 / 提亮字色也达不到 need（需要垫底）时为 True。"""
    dark = is_dark(tuple(int(c) for c in fill[:3]))
    return contrast(DARK_POLE if dark else LIGHT_POLE, worst_bg(img, box, dark)) < need


def text_box(x: float, y: float, w: float, size: int) -> tuple[float, float, float, float]:
    """一行字大致占的框（含上下少量余量）。"""
    return (x - size * 0.1, y, x + w + size * 0.1, y + size * 1.15)
