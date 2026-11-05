"""风格复刻提示词与离线预览：仅借用设计风格，商品主体保持原样。"""
from pathlib import Path

from PIL import Image, ImageOps

from app.services.compliance.ad_words import check_text


# 约束产品保真、设计参考范围与品牌文字隔离；备注再次合规替换。
def build_prompt(params: dict) -> str:
    note, _ = check_text(str(params.get("notes") or ""))
    return "。".join(part for part in [
        "产品引用图为商品主体，风格引用图为参考设计图，仅用于风格参考",
        "复刻参考图的构图、光影、配色、版式氛围；主体替换为产品图中的商品",
        "将本张所有产品图中的商品一起作为画面主体，保持商品外形、比例、颜色、logo和文字不变，不增删部件",
        "不要复制参考图里的品牌、logo与文字，不添加水印或广告语", note,
    ] if part)


# 提取参考设计主色作底，按居中一排展示全部商品，输出本地 PNG。
def generate_mock(reference: Path, products: list[Path], dest: Path, *, size: str) -> Path:
    width, height = (int(n) for n in size.split("x"))
    with Image.open(reference) as raw:
        palette = ImageOps.exif_transpose(raw).convert("RGB").resize((64, 64)).quantize(colors=8)
        dominant = max(palette.getcolors(), key=lambda entry: entry[0])[1]
        color = tuple(palette.getpalette()[dominant * 3:dominant * 3 + 3])
    output = Image.new("RGBA", (width, height), (*color, 255))
    slot_width = width * .8 / len(products)
    left = width * .1
    for idx, path in enumerate(products):
        with Image.open(path) as raw:
            product = ImageOps.exif_transpose(raw).convert("RGBA")
            product = ImageOps.contain(product, (max(1, round(slot_width * .9)), max(1, round(height * .8))), Image.Resampling.LANCZOS)
            x = round(left + idx * slot_width + (slot_width - product.width) / 2)
            output.alpha_composite(product, (x, (height - product.height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    output.save(dest, format="PNG")
    return dest
