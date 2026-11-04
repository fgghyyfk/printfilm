"""一图出套图 ·「丝巾详情页」品类：尺寸、槽位、模特/平铺参考、提示词与专用版式。

构成参照真丝方巾店铺常见详情页：封面（模特 + 竖排款名）、举巾展示整幅图案、面料特写、
多种系法、尺寸规格、近景、长条领结、背面披肩、街拍、平铺原图。
- ref="model"：参考图 1 = 固定模特（模特库），参考图 2 = 丝巾平铺图；生成后按平铺图自动校色
- ref="product"：只用平铺图作参考（面料特写）
- ref="flat"：不调模型，直接用平铺图排版（尺寸图、平铺图），不收费
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

CATEGORY = "scarf"
OUTPUT_SIZE = (1440, 1920)          # 3:4 竖图（淘宝 3:4 主图与详情通用）
GEN_RATIO = "3:4"

# 尺寸：id → (名称, 边长 cm, 佩戴提示)
SIZES: dict[str, tuple[str, int, str]] = {
    "53": ("53×53cm 小方巾", 53, "小方巾：折成细长条系在颈间做领巾，结小巧精致，也可系在包带或手腕上"),
    "68": ("68×68cm 小方巾", 68, "小方巾：对折成三角在颈前打一个小结，或折成长条绕颈一圈"),
    "88": ("88×88cm 中方巾", 88, "中方巾：对折成三角披在肩上、胸前打结，或折成长条做领结，垂坠长度到胸口"),
    "110": ("110×110cm 大方巾", 110, "大方巾：可完全展开斜披双肩、做三角大披肩覆盖肩背，或对折后在胸前打结，面积大、垂坠感强"),
    "135": ("135×135cm 大方巾", 135, "大尺寸方巾/披肩：可整条展开裹在肩上当披肩，对折成大三角披肩覆盖肩背，或在胸前松松打一个大结，面积大、包裹感强"),
}

# 材质：id → (名称, 质感描述, 模特造型)
MATERIALS: dict[str, tuple[str, str, str]] = {
    "silk": (
        "真丝",
        "真丝斜纹缎面质感，柔和自然的光泽与立体垂坠褶皱",
        "模特低盘发、戴小珍珠耳钉，穿米白色真丝吊带长裙或无袖上衣，衣服素净不抢丝巾；"
        "浅灰米色墙面背景，前景有少量虚化的绿植，柔和侧光，低饱和、通透高级",
    ),
    "silk_wool": (
        "丝毛混纺",
        "丝毛混纺面料质感：柔软细腻的羊毛呢面，哑光为主、只有很淡的柔光，表面有细微绒感，厚实温暖，垂坠柔和、褶皱圆润；"
        "不要真丝缎面那种强反光和锐利亮面",
        "模特低盘发、戴小珍珠耳钉，穿燕麦色或米白色羊绒针织衫、驼色大衣等秋冬素色衣着，衣服素净不抢丝巾；"
        "暖调浅米色墙面或室内窗边，柔和侧光，低饱和、温暖高级",
    ),
}
DEFAULT_MATERIAL = "silk"
DEFAULT_SIZE = "88"
COUNTS = (6, 8, 10)
DEFAULT_COUNT = 8

# 丝巾专用的底图约束（替代通用商品的「保持商品外形」）；质感按材质补在前面
LOCK_SUFFIX = (
    "丝巾的图案、配色与四边边框必须与丝巾平铺参考图完全一致，不重画图案、不偏色、不发黄、不发灰；"
    "画面中不要出现任何文字、字幕、标语、价格、水印或 logo；商业时尚摄影，高清锐利，真实质感"
)
MODEL_PREFIX = (
    "参考图1是固定模特：画面中的人物必须是图1这位模特本人，脸型、五官、发型、肤色与气质完全一致，不要换人；"
    "参考图2是丝巾的完整平铺图，是丝巾图案与颜色的唯一来源。"
)
PRODUCT_PREFIX = "参考图是丝巾的完整平铺图，是丝巾图案与颜色的唯一来源。"


def normalize_material(value: str | None) -> str:
    """规整材质 id，未知回落真丝。"""
    key = str(value or "").strip().lower()
    return key if key in MATERIALS else DEFAULT_MATERIAL


def material_label(value: str | None) -> str:
    """材质名称。"""
    return MATERIALS[normalize_material(value)][0]


def size_label(size: str | None) -> str:
    """尺寸名称，未知回落默认。"""
    return SIZES.get(normalize_size(size), SIZES[DEFAULT_SIZE])[0]


def normalize_size(value: str | None) -> str:
    """规整尺寸 id，未知回落默认。"""
    key = str(value or "").strip().lower().replace("cm", "")
    return key if key in SIZES else DEFAULT_SIZE


def normalize_count(value: int | str | None) -> int:
    """规整张数，只允许 6/8/10。"""
    try:
        n = int(value or DEFAULT_COUNT)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return n if n in COUNTS else DEFAULT_COUNT


def wear_hint(size: str | None) -> str:
    """该尺寸适合的佩戴方式（写进模特槽位提示词）。"""
    return SIZES[normalize_size(size)][2]


def build_prompt(*, slot_prompt: str, scene: str, ref: str, size: str | None, material: str | None = None) -> str:
    """拼装丝巾槽位的底图提示词（质感与模特造型随材质变化）。"""
    _, texture, look = MATERIALS[normalize_material(material)]
    prefix = MODEL_PREFIX if ref == "model" else PRODUCT_PREFIX
    parts = [prefix, slot_prompt, scene, f"丝巾规格：{size_label(size)}，{wear_hint(size)}" if ref == "model" else "",
             look if ref == "model" else "", texture, LOCK_SUFFIX]
    return "。".join(p.strip("。 ") for p in parts if p and p.strip())


def cover_copy(name: str, size: str | None, material: str | None = None) -> dict:
    """封面固定文案：竖排款名「XX·真丝方巾 / 丝毛方巾」+ 规格副标题（不用规划模型写的口号）。"""
    kind = "丝毛方巾" if normalize_material(material) == "silk_wool" else "真丝方巾"
    base = (name or "").strip("《》 ")
    for word in ("真丝方巾", "丝毛方巾", "方巾"):
        base = base.replace(word, "")
    base = base.strip(" ·") or (name or "")
    side = SIZES[normalize_size(size)][1]
    return {"title": f"{base}·{kind}"[:12], "subtitle": f"{material_label(material)} {side}cm", "bullets": []}


def plan_notes(size: str | None, notes: str, material: str | None = None) -> str:
    """给规划模型的补充信息：材质、尺寸与佩戴建议。"""
    head = (f"品类：{material_label(material)}方巾；规格：{size_label(size)}；适合的系法：{wear_hint(size)}；"
            f"面料质感：{MATERIALS[normalize_material(material)][1]}")
    return f"{head}。{notes.strip()}" if notes.strip() else head


# ---------- 专用版式 ----------

SERIF_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]
INK = (52, 48, 44)
MUTED = (120, 114, 108)
PAPER = (246, 244, 240)


def _serif(size: int):
    """宋体（衬线）字体；找不到时回落通用黑体。"""
    from PIL import ImageFont

    from app.services.ecom_set.layout import load_font

    for cand in SERIF_CANDIDATES:
        if Path(cand).exists():
            return ImageFont.truetype(cand, size=max(8, int(size)))
    return load_font(size, False)


def draw_vertical_title(img: Image.Image, *, title: str, subtitle: str) -> None:
    """左上竖排款名 + 细竖排副标题（封面版式），浅底用深色字、深底用白字。"""
    from app.services.ecom_set.layout import region_tone

    W, H = img.size
    x0, y0 = int(W * 0.07), int(H * 0.06)
    bright, _ = region_tone(img, (0, 0, int(W * 0.3), int(H * 0.5)))
    ink, muted = (INK, MUTED) if bright else ((255, 255, 255), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    t_font, s_font = _serif(int(W * 0.074)), _serif(int(W * 0.03))
    step = int(t_font.size * 1.12)
    title_chars = [c for c in title.replace(" ", "") if c][:12]
    col_x = x0 + int(s_font.size * 1.8)
    for i, ch in enumerate(title_chars):
        draw.text((col_x, y0 + i * step), "·" if ch in "·・." else ch, font=t_font, fill=ink)
    s_step = int(s_font.size * 1.25)
    for i, ch in enumerate([c for c in subtitle.replace(" ", "") if c][:20]):
        draw.text((x0, y0 + int(t_font.size * 0.2) + i * s_step), ch, font=s_font, fill=muted)


def _flat_canvas(product: Path, size: tuple[int, int], *, top_ratio: float, width_ratio: float = 0.78,
                 shift: float = 0.0) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """浅色画布上居中放平铺图（不裁切），返回 (画布, 图片所在框)。"""
    W, H = size
    canvas = Image.new("RGBA", size, PAPER + (255,))
    with Image.open(product) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")
    side = int(min(W * width_ratio, H * 0.56))
    im.thumbnail((side, side), Image.Resampling.LANCZOS)
    x, y = (W - im.width) // 2 - int(W * shift), int(H * top_ratio)
    canvas.paste(im, (x, y))
    return canvas, (x, y, x + im.width, y + im.height)


def render_size_spec(product: Path, out: Path, *, size_id: str | None, copy: dict) -> Path:
    """尺寸规格图：平铺图 + 两条尺寸标注线 + 规格文字（不调模型）。"""
    W, H = OUTPUT_SIZE
    canvas, (x1, y1, x2, y2) = _flat_canvas(product, OUTPUT_SIZE, top_ratio=0.2, width_ratio=0.68, shift=0.04)
    draw = ImageDraw.Draw(canvas)
    cm = SIZES[normalize_size(size_id)][1]
    label_font, title_font, sub_font = _serif(int(W * 0.03)), _serif(int(W * 0.05)), _serif(int(W * 0.026))
    title = str(copy.get("title") or "尺寸规格")
    draw.text(((W - title_font.getlength(title)) / 2, int(H * 0.07)), title, font=title_font, fill=INK)
    sub = str(copy.get("subtitle") or size_label(size_id))
    draw.text(((W - sub_font.getlength(sub)) / 2, int(H * 0.07) + int(title_font.size * 1.5)), sub, font=sub_font, fill=MUTED)
    gap, tick = int(W * 0.03), int(W * 0.012)
    # 横向标注
    yy = y2 + gap
    draw.line((x1, yy, x2, yy), fill=INK, width=3)
    for xx in (x1, x2):
        draw.line((xx, yy - tick, xx, yy + tick), fill=INK, width=3)
    text = f"{cm} cm"
    draw.text(((x1 + x2 - label_font.getlength(text)) / 2, yy + tick * 1.5), text, font=label_font, fill=INK)
    # 纵向标注
    xx = x2 + gap
    draw.line((xx, y1, xx, y2), fill=INK, width=3)
    for y in (y1, y2):
        draw.line((xx - tick, y, xx + tick, y), fill=INK, width=3)
    draw.text((xx + tick * 1.5, (y1 + y2) / 2 - label_font.size / 2), text, font=label_font, fill=INK)
    bullets = [str(b) for b in copy.get("bullets") or [] if str(b).strip()][:3]
    by = yy + tick * 1.5 + int(label_font.size * 2.6)
    for line in bullets:
        draw.text(((W - sub_font.getlength(line)) / 2, by), line, font=sub_font, fill=MUTED)
        by += int(sub_font.size * 1.7)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, format="JPEG", quality=93)
    return out


def _busiest_box(im: Image.Image, frac: float) -> tuple[int, int, int, int]:
    """在图上找细节最丰富的正方形区域（灰度标准差最大），用于纹样局部特写。"""
    from PIL import ImageStat

    W, H = im.size
    side = int(min(W, H) * frac)
    gray = im.convert("L").resize((W // 4, H // 4))
    best, box = -1.0, (0, 0, side, side)
    step = max(1, side // 6)
    for y in range(0, H - side + 1, step):
        for x in range(0, W - side + 1, step):
            g = gray.crop((x // 4, y // 4, (x + side) // 4, (y + side) // 4))
            std = ImageStat.Stat(g).stddev[0]
            if std > best:
                best, box = std, (x, y, x + side, y + side)
    return box


def render_crop(product: Path, out: Path, *, copy: dict) -> Path:
    """纹样细节：从平铺原图裁出细节最丰富的局部放大，底部一行说明（不调模型）。"""
    W, H = OUTPUT_SIZE
    canvas = Image.new("RGBA", OUTPUT_SIZE, PAPER + (255,))
    with Image.open(product) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")
    crop = im.crop(_busiest_box(im, 0.42))
    area_h = int(H * 0.8)
    canvas.paste(ImageOps.fit(crop, (W, area_h), Image.Resampling.LANCZOS), (0, 0))
    draw = ImageDraw.Draw(canvas)
    title_font, sub_font = _serif(int(W * 0.045)), _serif(int(W * 0.026))
    title, sub = str(copy.get("title") or "纹样细节"), str(copy.get("subtitle") or "")
    y = area_h + int(H * 0.05)
    draw.text(((W - title_font.getlength(title)) / 2, y), title, font=title_font, fill=INK)
    if sub:
        draw.text(((W - sub_font.getlength(sub)) / 2, y + int(title_font.size * 1.6)), sub, font=sub_font, fill=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, format="JPEG", quality=93)
    return out


def render_flat(product: Path, out: Path, *, copy: dict) -> Path:
    """平铺原图页：浅底居中平铺图 + 底部一行说明（不调模型）。"""
    W, H = OUTPUT_SIZE
    canvas, (_, _, _, y2) = _flat_canvas(product, OUTPUT_SIZE, top_ratio=0.16)
    draw = ImageDraw.Draw(canvas)
    title_font, sub_font = _serif(int(W * 0.045)), _serif(int(W * 0.026))
    title, sub = str(copy.get("title") or ""), str(copy.get("subtitle") or "")
    y = y2 + int(H * 0.05)
    if title:
        draw.text(((W - title_font.getlength(title)) / 2, y), title, font=title_font, fill=INK)
        y += int(title_font.size * 1.6)
    if sub:
        draw.text(((W - sub_font.getlength(sub)) / 2, y), sub, font=sub_font, fill=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, format="JPEG", quality=93)
    return out
