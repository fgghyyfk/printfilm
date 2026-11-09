"""一图出套图：用 Pillow 把文案排到无字底图上，并裁切到平台尺寸。"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from app.config import get_settings

# 字体候选：Docker 里装 fonts-noto-cjk（思源黑体同源，OFL）；本机 macOS 回落苹方/黑体
FONT_CANDIDATES = {
    True: [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
    False: [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
}
DARK = (24, 24, 27)
LIGHT = (255, 255, 255)
ACCENT_DARK = (190, 30, 45)


# 找可用的中文字体路径（bold=True 优先粗体）
@lru_cache(maxsize=4)
def font_path(bold: bool) -> str:
    custom = (get_settings().ecom_font_path or "").strip()
    if custom and Path(custom).exists():
        return custom
    for cand in FONT_CANDIDATES[bold]:
        if Path(cand).exists():
            return cand
    raise RuntimeError("未找到中文字体，请安装 fonts-noto-cjk 或配置 ECOM_FONT_PATH")


# 按字号加载字体（带缓存）
@lru_cache(maxsize=64)
def load_font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    path = font_path(bold)
    # Noto CJK 合集里 index 2 才是简体中文字形（0 是日文）
    index = 2 if "NotoSansCJK" in path or "NotoSerifCJK" in path else 0
    return ImageFont.truetype(path, size=max(8, int(size)), index=index)


# 等比缩放并居中裁切到目标尺寸
def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


# 按最大宽度把中文文本折行（逐字测量），最多 max_lines 行，超出用省略号
def wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for ch in text:
        if font.getlength(cur + ch) <= max_w:
            cur += ch
            continue
        lines.append(cur)
        cur = ch
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = sum(len(x) for x in lines)
    if consumed < len(text) and lines:
        last = lines[-1]
        while last and font.getlength(last + "…") > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


# 自动缩小字号直到在 max_lines 行内放下
def fit_lines(text: str, size: int, bold: bool, max_w: int, max_lines: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    s = size
    while s > 12:
        font = load_font(s, bold)
        lines = wrap(text, font, max_w, max_lines + 1)
        if len(lines) <= max_lines and not lines[-1].endswith("…"):
            return font, lines
        s = int(s * 0.9)
    font = load_font(s, bold)
    return font, wrap(text, font, max_w, max_lines)


# 统计区域亮度均值与纹理复杂度，决定字色与是否加底板
def region_tone(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[bool, bool]:
    region = img.crop(box).convert("L")
    stat = ImageStat.Stat(region)
    bright = stat.mean[0] >= 150
    busy = stat.stddev[0] >= 42
    return bright, busy


# 在指定区域画半透明底板（圆角）
def draw_panel(img: Image.Image, box: tuple[int, int, int, int], *, light: bool, alpha: int = 170) -> None:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    color = (255, 255, 255, alpha) if light else (0, 0, 0, alpha)
    radius = max(8, (box[3] - box[1]) // 8)
    ImageDraw.Draw(overlay).rounded_rectangle(box, radius=radius, fill=color)
    img.alpha_composite(overlay)


# 画一组居中或左对齐的文本行，返回占用的底部 y
def draw_lines(
    draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont, *,
    x: int, y: int, width: int, align: str, fill: tuple[int, int, int], gap: float = 0.25,
) -> int:
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * (1 + gap))
    for line in lines:
        w = font.getlength(line)
        lx = x + (width - w) / 2 if align == "center" else x
        draw.text((lx, y), line, font=font, fill=fill)
        y += lh
    return y


# 画一排胶囊标签（卖点要点），自动换行居中
def draw_pills(img: Image.Image, bullets: list[str], *, top: int, width: int, size: int, dark_text: bool) -> int:
    if not bullets:
        return top
    font = load_font(size, False)
    draw = ImageDraw.Draw(img)
    pad_x, pad_y, gap = int(size * 0.8), int(size * 0.45), int(size * 0.6)
    pills = [(b, int(font.getlength(b)) + pad_x * 2) for b in bullets]
    h = size + pad_y * 2
    rows: list[list[tuple[str, int]]] = [[]]
    for p in pills:
        row_w = sum(w for _, w in rows[-1]) + gap * len(rows[-1])
        if rows[-1] and row_w + p[1] > width * 0.9:
            rows.append([])
        rows[-1].append(p)
    y = top
    for row in rows:
        total = sum(w for _, w in row) + gap * (len(row) - 1)
        x = (img.width - total) // 2
        for text, w in row:
            bg = (255, 255, 255, 215) if dark_text else (0, 0, 0, 150)
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
            img.alpha_composite(overlay)
            draw = ImageDraw.Draw(img)
            draw.text((x + pad_x, y + pad_y - size * 0.1), text, font=font, fill=DARK if dark_text else LIGHT)
            x += w + gap
        y += h + gap
    return y


def render(
    base_path: Path,
    out_path: Path,
    *,
    size: tuple[int, int],
    layout: str,
    blank: str,
    copy: dict | None,
    type_style: str = "bold",
) -> Path:
    """把文案按版式排到底图上，裁到 size 并保存为 JPEG。

    layout: none | title_corner | title_sub | title_bullets | label_bar | vertical_title（丝巾封面竖排）；blank: top | bottom | none。
    type_style: bold 促销醒目（粗黑体）| serif 典雅衬线 | sans 简约黑体（见 typo.TYPE_STYLES）。
    """
    with Image.open(base_path) as src:
        img = cover(ImageOps.exif_transpose(src).convert("RGBA"), size)
    body = copy or {}
    title = str(body.get("title") or "").strip()
    subtitle = str(body.get("subtitle") or "").strip()
    bullets = [str(b).strip() for b in body.get("bullets") or [] if str(b).strip()]
    en = str(body.get("en") or "").strip()
    if layout == "vertical_title" and (title or subtitle):
        from app.services.ecom_set.scarf import draw_vertical_title

        draw_vertical_title(img, title=title, subtitle=subtitle)
    elif layout not in {"none", "vertical_title"} and (title or subtitle or bullets):
        from app.services.ecom_set import typo

        style = typo.TYPE_STYLES[typo.normalize_type_style(type_style)]
        if style.id == "bold":
            _draw_layout(img, layout=layout, blank=blank, title=title, subtitle=subtitle, bullets=bullets)
        else:
            typo.draw_refined(img, style=style, layout=layout, blank=blank, title=title, subtitle=subtitle,
                              bullets=bullets, en=en)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="JPEG", quality=92)
    return out_path


# 各版式的具体排版
def _draw_layout(img: Image.Image, *, layout: str, blank: str, title: str, subtitle: str, bullets: list[str]) -> None:
    W, H = img.size
    margin = int(W * 0.07)
    inner_w = W - margin * 2
    at_bottom = blank == "bottom" or layout == "label_bar"
    zone_h = int(H * (0.26 if layout == "label_bar" else 0.32))
    zone = (0, H - zone_h, W, H) if at_bottom else (0, 0, W, zone_h)
    bright, busy = region_tone(img, zone)
    dark_text = bright
    align = "left" if layout == "title_corner" else "center"

    t_font, t_lines = fit_lines(title, int(W * 0.085), True, inner_w, 2) if title else (None, [])
    s_font, s_lines = fit_lines(subtitle, int(W * 0.042), False, inner_w, 2) if subtitle else (None, [])
    pills = bullets if layout == "title_bullets" else []
    pill_size = int(W * 0.034)

    # 预估内容高度，用于底部对齐和底板
    def block_h(font: ImageFont.FreeTypeFont | None, n: int, gap: float = 0.25) -> int:
        if not font or not n:
            return 0
        a, d = font.getmetrics()
        return int((a + d) * (1 + gap)) * n
    pill_rows = 0 if not pills else (1 if len(pills) <= 2 else 2)
    content_h = (
        block_h(t_font, len(t_lines)) + block_h(s_font, len(s_lines))
        + (pill_rows * int(pill_size * 2.6) + int(pill_size * 0.6) if pills else 0)
    )
    pad = int(W * 0.035)
    top = (H - margin - content_h) if at_bottom else margin

    if layout == "label_bar" or busy:
        panel_light = bright if layout != "label_bar" else False
        box = (margin - pad, top - pad, W - margin + pad, top + content_h + pad)
        if layout == "label_bar":
            box = (0, top - pad * 2, W, H)
        draw_panel(img, box, light=panel_light, alpha=150 if layout == "label_bar" else 175)
        dark_text = panel_light

    draw = ImageDraw.Draw(img)
    fill = DARK if dark_text else LIGHT
    y = top
    if t_lines and t_font:
        if not busy and layout != "label_bar":
            _shadow(img, t_lines, t_font, x=margin, y=y, width=inner_w, align=align, dark_text=dark_text)
            draw = ImageDraw.Draw(img)
        y = draw_lines(draw, t_lines, t_font, x=margin, y=y, width=inner_w, align=align, fill=fill)
    if s_lines and s_font:
        sub_fill = (ACCENT_DARK if dark_text and layout == "title_sub" else fill)
        y = draw_lines(draw, s_lines, s_font, x=margin, y=y, width=inner_w, align=align, fill=sub_fill)
    if pills:
        draw_pills(img, pills, top=y + int(pill_size * 0.6), width=W, size=pill_size, dark_text=dark_text)


# 给浅底以外的标题加柔和投影，提升可读性
def _shadow(img: Image.Image, lines: list[str], font: ImageFont.FreeTypeFont, *, x: int, y: int, width: int, align: str, dark_text: bool) -> None:
    if dark_text:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    off = max(2, font.size // 24)
    draw_lines(d, lines, font, x=x + off, y=y + off, width=width, align=align, fill=(0, 0, 0))
    layer = layer.filter(ImageFilter.GaussianBlur(off * 2))
    alpha = layer.getchannel("A").point(lambda v: int(v * 0.55))
    layer.putalpha(alpha)
    img.alpha_composite(layer)


# 无 Key 联调：把商品图贴到渐变背景上，生成一张可排版的占位底图
def mock_base(product_path: Path, out_path: Path, *, ratio: str, seed: str) -> Path:
    W, H = (1080, 1080) if ratio == "1:1" else (1080, 1440)
    hue = int(hashlib.md5(seed.encode()).hexdigest()[:2], 16)
    top_c = (200 + hue % 40, 205 + hue % 30, 215)
    bottom_c = (120 + hue % 60, 130 + hue % 50, 150)
    bg = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(bg)
    for yy in range(H):
        t = yy / H
        draw.line([(0, yy), (W, yy)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top_c, bottom_c)))
    with Image.open(product_path) as p:
        prod = ImageOps.exif_transpose(p).convert("RGBA")  # 透明底抠图按 alpha 贴，避免出现白框/黑框
        prod.thumbnail((int(W * 0.6), int(H * 0.5)))
    bg.paste(prod, ((W - prod.width) // 2, int(H * 0.42)), prod.getchannel("A"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, format="JPEG", quality=90)
    return out_path
