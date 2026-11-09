"""电商图排版：字体风格预设（典雅衬线 / 简约黑体 / 促销醒目）、字距排字、精致版式。

「促销醒目」沿用 layout._draw_layout 的粗黑体版式；另外两种走本文件的 draw_refined。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

NOTO_DIR = "/usr/share/fonts/opentype/noto"
# Noto CJK 的 ttc 里第 3 个字面（index 2）才是简体中文（SC），0 是日文字形
NOTO_SC_INDEX = 2
FAMILY_FILES = {
    ("serif", False): "NotoSerifCJK-Regular.ttc",
    ("serif", True): "NotoSerifCJK-Bold.ttc",
    ("sans", False): "NotoSansCJK-Regular.ttc",
    ("sans", True): "NotoSansCJK-Bold.ttc",
}
# 本机（macOS）没有 Noto CJK 时的回落
MAC_FALLBACK = {
    "serif": ["/System/Library/Fonts/Supplemental/Songti.ttc", "/System/Library/Fonts/STHeiti Light.ttc"],
    "sans": ["/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/PingFang.ttc"],
}


@dataclass(frozen=True)
class TypeStyle:
    """一套字体风格：字体、字号（占画面宽）、字距（em）与卖点样式。"""

    id: str
    name: str
    hint: str
    title_family: str = "serif"
    title_bold: bool = False
    title_size: float = 0.058
    title_track: float = 0.06
    sub_family: str = "sans"
    sub_size: float = 0.031
    sub_track: float = 0.12
    en_size: float = 0.021
    en_track: float = 0.32
    pill: str = "outline"   # outline 细线框 | soft 浅色底 | fill（促销版式自带）
    pill_size: float = 0.027
    rule: bool = True       # 标题下的细分隔线


TYPE_STYLES: dict[str, TypeStyle] = {
    "serif": TypeStyle("serif", "典雅衬线", "宋体细字、留白多，适合香氛、护肤、茶酒、珠宝"),
    "sans": TypeStyle("sans", "简约黑体", "黑体常规字重，清爽现代，适合 3C、食品、日用", title_family="sans",
                      title_size=0.062, title_track=0.04, sub_size=0.032, sub_track=0.08, pill="soft", rule=False),
    "bold": TypeStyle("bold", "促销醒目", "粗黑体大字，适合大促、活动图", title_family="sans", title_bold=True,
                      title_size=0.085, title_track=0.0, sub_size=0.042, sub_track=0.0, pill="fill", rule=False),
}
DEFAULT_TYPE_STYLE = "bold"
# 场景行业 → 默认字体风格（日化/标品用）
INDUSTRY_TYPE = {"food": "sans", "digital": "sans"}

NO_LINE_START = set("。，、；：！？）》」』”’,.;:!?)")
INK = (38, 34, 30)
INK_MUTED = (112, 103, 95)
PAPER = (255, 255, 255)
PAPER_MUTED = (236, 232, 226)


def normalize_type_style(value: str | None, default: str = DEFAULT_TYPE_STYLE) -> str:
    """规整字体风格 id，未知回落 default。"""
    key = (value or "").strip()
    return key if key in TYPE_STYLES else default


def default_type_style(category: str, scene: dict | None) -> str:
    """品类与场景决定的默认字体风格：日化/标品按场景行业（食品、3C 用黑体，其余衬线）；其它品类保持粗黑体。"""
    if category != "goods":
        return DEFAULT_TYPE_STYLE
    spec = (scene or {}).get("spec") or {}
    if spec.get("type_style") in TYPE_STYLES:
        return spec["type_style"]
    industries = list((scene or {}).get("industries") or [])
    for ind in industries:
        if ind in INDUSTRY_TYPE:
            return INDUSTRY_TYPE[ind]
    return "serif"


def options() -> list[dict]:
    """前端选项。"""
    return [{"id": s.id, "name": s.name, "hint": s.hint} for s in TYPE_STYLES.values()]


# ---------- 字体 ----------
def _font_dirs() -> list[str]:
    extra = (os.environ.get("ECOM_FONT_DIR") or "").strip()
    return [d for d in (extra, NOTO_DIR) if d]


@lru_cache(maxsize=8)
def _font_file(family: str, bold: bool) -> tuple[str, int]:
    """找某字族的字体文件，返回 (路径, ttc 字面序号)。"""
    name = FAMILY_FILES[(family, bold)]
    for d in _font_dirs():
        p = Path(d) / name
        if p.exists():
            return str(p), NOTO_SC_INDEX
    for cand in MAC_FALLBACK[family]:
        if Path(cand).exists():
            return cand, 0
    from app.services.ecom_set.layout import font_path

    return font_path(bold), 0


@lru_cache(maxsize=128)
def font(family: str, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    """按字族/字重/字号加载字体（带缓存）。"""
    path, index = _font_file(family, bold)
    try:
        return ImageFont.truetype(path, size=max(8, int(size)), index=index)
    except OSError:
        return ImageFont.truetype(path, size=max(8, int(size)))


# ---------- 字距排字 ----------
def text_width(text: str, f: ImageFont.FreeTypeFont, track: float) -> float:
    """带字距的文本宽度（track 单位 em）。"""
    if not text:
        return 0.0
    return f.getlength(text) + f.size * track * (len(text) - 1)


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, f: ImageFont.FreeTypeFont,
              fill, track: float) -> None:
    """逐字画带字距的文本；无字距时整行画（保留字偶间距）。"""
    if track <= 0.001:
        draw.text(xy, text, font=f, fill=fill)
        return
    x, y = xy
    gap = f.size * track
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + gap


def wrap(text: str, f: ImageFont.FreeTypeFont, track: float, max_w: int, max_lines: int) -> list[str]:
    """按宽度折行：优先在空格处断开英文，中文逐字；超出 max_lines 用省略号。"""
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(text):
        ch = text[i]
        # 英文单词整体放
        if ch.isascii() and ch.isalnum():
            j = i
            while j < len(text) and text[j].isascii() and text[j].isalnum():
                j += 1
            token = text[i:j]
        else:
            token, j = ch, i + 1
        # 句末标点不放行首（避头尾），宁可略超一点宽度
        if text_width(cur + token, f, track) <= max_w or not cur or token in NO_LINE_START:
            cur += token
        else:
            lines.append(cur.rstrip())
            cur = token.lstrip()
            if len(lines) >= max_lines:
                break
        i = j
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if "".join(lines).replace(" ", "") != text.replace(" ", "") and lines:
        last = lines[-1]
        while last and text_width(last + "…", f, track) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def fit(text: str, family: str, bold: bool, size: int, track: float, max_w: int, max_lines: int,
        ) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """自动缩小字号直到在 max_lines 行内完整放下。"""
    s = size
    while s > 14:
        f = font(family, bold, s)
        lines = wrap(text, f, track, max_w, max_lines + 1)
        if len(lines) <= max_lines and not lines[-1].endswith("…"):
            return f, lines
        s = int(s * 0.92)
    f = font(family, bold, s)
    return f, wrap(text, f, track, max_w, max_lines)


def line_h(f: ImageFont.FreeTypeFont, gap: float) -> int:
    a, d = f.getmetrics()
    return int((a + d) * (1 + gap))


# ---------- 精致版式 ----------
def _tone(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[bool, float]:
    """区域是否偏亮、纹理复杂度（亮度标准差）。"""
    st = ImageStat.Stat(img.crop(box).convert("L"))
    return st.mean[0] >= 140, st.stddev[0]


def _scrim(img: Image.Image, box: tuple[int, int, int, int], *, light: bool, strength: float, from_top: bool) -> None:
    """文字区加一层渐变雾面（比圆角底板更自然），strength 为最浓处不透明度。"""
    x0, y0, x1, y1 = box
    h = max(1, y1 - y0)
    col = (250, 247, 242) if light else (20, 18, 16)
    grad = Image.new("L", (1, h))
    for i in range(h):
        t = i / h if from_top else 1 - i / h
        # 文字区前 60% 保持浓度，之后渐隐
        k = 1.0 if t < 0.6 else max(0.0, 1 - (t - 0.6) / 0.4)
        grad.putpixel((0, i), int(255 * strength * k))
    layer = Image.new("RGBA", (x1 - x0, h), (*col, 255))
    layer.putalpha(grad.resize((x1 - x0, h)))
    img.alpha_composite(layer, (x0, y0))


def draw_refined(img: Image.Image, *, style: TypeStyle, layout: str, blank: str, title: str, subtitle: str,
                 bullets: list[str], en: str) -> None:
    """衬线 / 黑体精致版式：英文小标 → 标题 → 细线 → 副标题 → 卖点。"""
    W, H = img.size
    margin = int(W * 0.08)
    inner_w = W - margin * 2
    label_bar = layout == "label_bar"
    at_bottom = blank == "bottom" or label_bar
    align = "left" if layout == "title_corner" else "center"
    scale = 0.8 if label_bar else 1.0

    en_text = en.upper()
    pills = bullets if layout == "title_bullets" else []
    gap_s = int(W * 0.018)
    # 文字区高度上限：留白区约占画面 1/4，超出就整体缩小，避免压到商品
    max_h = int(H * (0.2 if label_bar else 0.21))
    for _ in range(6):
        en_f = font("serif", False, int(W * style.en_size * scale)) if en else None
        t_f, t_lines = (fit(title, style.title_family, style.title_bold, int(W * style.title_size * scale),
                            style.title_track, inner_w, 2) if title else (None, []))
        s_f, s_lines = (fit(subtitle, style.sub_family, False, int(W * style.sub_size * scale), style.sub_track,
                            inner_w, 2) if subtitle else (None, []))
        p_f = font("sans", False, int(W * style.pill_size * scale)) if pills else None
        blocks: list[tuple[str, int]] = []
        if en_f:
            blocks.append(("en", line_h(en_f, 0.2)))
        if t_lines and t_f:
            blocks.append(("title", line_h(t_f, 0.32) * len(t_lines)))
        if style.rule and t_lines and (s_lines or pills):
            blocks.append(("rule", int(W * 0.024 * scale)))
        if s_lines and s_f:
            blocks.append(("sub", line_h(s_f, 0.5) * len(s_lines)))
        if pills and p_f:
            blocks.append(("pills", int(p_f.size * 2.4)))
        content_h = sum(h for _, h in blocks) + int(gap_s * scale) * max(0, len(blocks) - 1)
        if content_h <= max_h:
            break
        scale *= 0.9
    gap_s = int(gap_s * scale)

    top_pad = int(H * 0.05)
    top = (H - int(H * 0.05) - content_h) if at_bottom else top_pad
    zone = (0, max(0, top - top_pad), W, min(H, top + content_h + top_pad))
    bright, busy = _tone(img, zone)
    dark_text = bright
    if label_bar:
        _scrim(img, (0, zone[1], W, H), light=True, strength=0.86, from_top=False)
        dark_text = True
    elif busy > 38:
        _scrim(img, (0, 0, W, zone[3] + top_pad) if not at_bottom else (0, zone[1] - top_pad, W, H),
               light=bright, strength=0.45 if bright else 0.28, from_top=not at_bottom)
    ink, muted = (INK, INK_MUTED) if dark_text else (PAPER, PAPER_MUTED)

    draw = ImageDraw.Draw(img)

    def x_for(w: float) -> float:
        return margin + (inner_w - w) / 2 if align == "center" else margin

    y = top
    for kind, h in blocks:
        if kind == "en" and en_f:
            draw_text(draw, (x_for(text_width(en_text, en_f, style.en_track)), y), en_text, en_f, muted, style.en_track)
        elif kind == "title" and t_f:
            ty = y
            for line in t_lines:
                draw_text(draw, (x_for(text_width(line, t_f, style.title_track)), ty), line, t_f, ink, style.title_track)
                ty += line_h(t_f, 0.32)
        elif kind == "rule":
            rw = int(W * 0.05)
            rx = x_for(rw)
            ry = y + h // 2
            draw.line([(rx, ry), (rx + rw, ry)], fill=muted, width=max(1, W // 700))
        elif kind == "sub" and s_f:
            sy = y
            for line in s_lines:
                draw_text(draw, (x_for(text_width(line, s_f, style.sub_track)), sy), line, s_f, muted, style.sub_track)
                sy += line_h(s_f, 0.5)
        elif kind == "pills" and p_f:
            _draw_pills(img, pills, p_f, y=y, width=W, style=style, dark_text=dark_text, align=align, margin=margin)
            draw = ImageDraw.Draw(img)
        y += h + gap_s


def _draw_pills(img: Image.Image, bullets: list[str], f: ImageFont.FreeTypeFont, *, y: int, width: int,
                style: TypeStyle, dark_text: bool, align: str, margin: int) -> None:
    """卖点：细线框（衬线风）或浅底（黑体风），一行排开，放不下就缩小间距。"""
    track = 0.06
    pad_x, pad_y = int(f.size * 0.9), int(f.size * 0.42)
    gap = int(f.size * 0.7)
    ws = [int(text_width(b, f, track)) + pad_x * 2 for b in bullets]
    total = sum(ws) + gap * (len(ws) - 1)
    while total > width - margin * 2 and gap > 4:
        gap = int(gap * 0.7)
        total = sum(ws) + gap * (len(ws) - 1)
    x = (width - total) // 2 if align == "center" else margin
    h = f.size + pad_y * 2
    ink = INK if dark_text else PAPER
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for b, w in zip(bullets, ws, strict=True):
        box = (x, y, x + w, y + h)
        if style.pill == "outline":
            d.rounded_rectangle(box, radius=h // 2, outline=(*ink, 150), width=max(1, f.size // 18))
        else:
            d.rounded_rectangle(box, radius=h // 2, fill=(255, 255, 255, 190) if dark_text else (0, 0, 0, 110))
        x += w + gap
    img.alpha_composite(layer)
    draw = ImageDraw.Draw(img)
    x = (width - total) // 2 if align == "center" else margin
    for b, w in zip(bullets, ws, strict=True):
        draw_text(draw, (x + pad_x, y + pad_y - f.size * 0.08), b, f, ink, track)
        x += w + gap
