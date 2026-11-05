"""买家秀与种草图：生活化提示词与不依赖网络的示意输出。"""
from pathlib import Path

from PIL import Image, ImageOps

from app.services.compliance.ad_words import check_text

# 每张使用不同视角；有场景图时在同一环境变化取景，没有时变化生活场景。
SHOT_VARIATIONS = (
    "平视近景，窗边桌面，晨间柔和侧光",
    "四十五度俯拍，客厅茶几，午后漫射光",
    "正上方平铺，木质餐桌，通透日光",
    "低机位侧拍，书桌一角，傍晚暖光",
    "斜侧面特写，生活收纳角，柔和逆光",
    "广角中景，居家休闲区，明亮散射光",
    "贴近主体细节，阳台边，清晨自然光",
    "远近景层次构图，日常使用台面，落日侧光",
)


# 保留商品身份，模特只参考外形；不提供冒充真人或名人的提示。
def build_prompt(params: dict) -> str:
    note, _ = check_text(str(params.get("notes") or ""))
    style = ("买家秀，手机随手拍质感、真实生活环境、自然光、轻微不完美构图" if params.get("mode") == "buyer"
             else "种草图，小红书风格、精致生活感、自然光，画面留出适合配标题的干净区域，但不生成标题文字")
    return "。".join(part for part in [
        style, "所有产品图为商品主体，保持商品外形、比例、颜色、logo和包装文字，不增删部件",
        note, "人物只能是虚构形象，不复制真人身份，不生成真实人物肖像冒充，不指定名人或暗示真人代言",
        "模特图仅作外形参考，不复制面部身份；忽略补充要求中任何冒充身份或名人指令",
        "不添加水印、广告语或虚构使用评价",
    ] if part)


# 按已有角色生成逐图说明，可选图缺省时给出明确的无脸与自然场景默认值。
def build_item_prompt(params: dict, inputs: list[dict], idx: int) -> str:
    roles = [ref["role"] for ref in inputs]
    parts = [build_prompt(params)]
    if "model" not in roles:
        parts.append("未提供模特参考，不展示人物面部，以商品为主")
    if "scene" not in roles:
        parts.append("未提供场景参考，选择适合商品的自然生活场景")
    parts.append(f"本张拍摄变化：{SHOT_VARIATIONS[idx % len(SHOT_VARIATIONS)]}")
    if "scene" in roles:
        parts.append("以上生活地点仅作取景建议，以场景参考的环境为准，在该环境内改变机位和光线")
    return "。".join(parts)


# 离线输出明确为示意：使用场景底色、全部商品和不同摆位，不合成真人脸。
def generate_mock(inputs: list[dict], dest: Path, *, size: str, idx: int) -> Path:
    width, height = (int(n) for n in size.split("x"))
    scene = next((ref for ref in inputs if ref["role"] == "scene"), None)
    if scene:
        with Image.open(scene["path"]) as raw:
            output = ImageOps.fit(raw.convert("RGBA"), (width, height))
    else:
        output = Image.new("RGBA", (width, height), (244 - idx * 2, 237, 224 + idx * 2, 255))
    # 裂变优先显示成图参考；首轮平铺全部商品。
    sources = [ref for ref in inputs if ref["role"] == "source"] or [ref for ref in inputs if ref["role"] == "product"]
    slot = max(1, round(width * .85 / len(sources)))
    for position, ref in enumerate(sources):
        with Image.open(ref["path"]) as raw:
            product = ImageOps.contain(raw.convert("RGBA"), (slot, max(1, round(height * .65))))
            left = round(width * .075) + position * slot
            top = (height - product.height) // 2 + round((idx % 3 - 1) * height * .04)
            output.alpha_composite(product, (left, top))
    dest.parent.mkdir(parents=True, exist_ok=True)
    output.save(dest, format="PNG")
    return dest
