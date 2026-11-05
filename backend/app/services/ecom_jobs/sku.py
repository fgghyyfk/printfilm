"""SKU 替换的场景画幅、约束提示词与离线示意图。"""
from pathlib import Path

from PIL import Image, ImageOps

from app.services.compliance.ad_words import check_text

# 复用电商已开放的四种画幅，等距离时按此顺序确定结果。
RATIOS = ("1:1", "3:4", "9:16", "16:9")


# 按宽高比数值的绝对差选择最近画幅；调用方传 EXIF 方向校正后的尺寸。
def nearest_ratio(size: tuple[int, int]) -> str:
    value = size[0] / size[1]
    return min(RATIOS, key=lambda ratio: abs(value - int(ratio.split(":")[0]) / int(ratio.split(":")[1])))


# 场景为不可重设计的原图，只替换指定主体并保留新 SKU 外观。
def build_prompt(params: dict) -> str:
    note, _ = check_text(str(params.get("notes") or ""))
    return "。".join(part for part in [
        "场景图为被替换的原图，保持构图、背景、光影、道具、机位不变，不重新设计场景",
        "只替换场景中的主体商品为本张产品图中的商品，不增加第二件商品，不改变其它物体",
        "如有区域描述，只替换该区域指定的商品，其余商品保持不变",
        "新产品外形、颜色、logo、包装文字与部件必须准确，尺寸比例合理，匹配原位置的透视与接触阴影",
        "不添加文字、水印或广告语", note,
    ] if part)


# 离线示意：按场景画幅保留背景，居中叠放单个 SKU；不模拟智能区域识别。
def generate_mock(scene: Path, product: Path, dest: Path, *, size: str) -> Path:
    width, height = (int(n) for n in size.split("x"))
    with Image.open(scene) as raw:
        output = ImageOps.fit(ImageOps.exif_transpose(raw).convert("RGBA"), (width, height), method=Image.Resampling.LANCZOS)
    with Image.open(product) as raw:
        subject = ImageOps.contain(ImageOps.exif_transpose(raw).convert("RGBA"),
                                   (max(1, round(width * .5)), max(1, round(height * .6))), Image.Resampling.LANCZOS)
        output.alpha_composite(subject, ((width - subject.width) // 2, (height - subject.height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    output.save(dest, format="PNG")
    return dest
