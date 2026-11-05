"""本地 mock 精修输出，不依赖模型、Key、OSS 或网络。"""
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps


# 将参考商品图居中放入指定画幅并提升清晰度，输出本地 PNG。
def generate_mock(source: Path, dest: Path, *, size: str, background: str) -> Path:
    width, height = (int(n) for n in size.split("x"))
    with Image.open(source) as raw:
        product = ImageOps.exif_transpose(raw).convert("RGBA")
        if background == "keep":
            output = ImageOps.fit(product, (width, height), method=Image.Resampling.LANCZOS)
        else:
            output = Image.new("RGBA", (width, height), "white")
            product = ImageOps.contain(product, (max(1, round(width * .8)), max(1, round(height * .8))), Image.Resampling.LANCZOS)
            product = ImageEnhance.Sharpness(product).enhance(1.2)
            output.alpha_composite(product, ((width - product.width) // 2, (height - product.height) // 2))
        dest.parent.mkdir(parents=True, exist_ok=True)
        output.save(dest, format="PNG")
    return dest
