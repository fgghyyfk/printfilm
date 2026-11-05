"""一图出套图：平台尺寸、风格、槽位与默认套图构成（纯数据 + 查询函数）。"""

from __future__ import annotations

from dataclasses import dataclass

# 平台：id → (名称, 主图像素, 详情/卖点图像素)。上线前需按各平台最新规则核对
PLATFORMS: dict[str, tuple[str, tuple[int, int], tuple[int, int]]] = {
    "taobao": ("淘宝/天猫", (800, 800), (750, 1000)),
    "jd": ("京东", (800, 800), (750, 1000)),
    "pdd": ("拼多多", (750, 750), (750, 1000)),
    "douyin": ("抖音", (800, 800), (750, 1000)),
    "xhs": ("小红书", (1080, 1440), (1080, 1440)),
    "amazon": ("Amazon", (2000, 2000), (1500, 2000)),
}
DEFAULT_PLATFORM = "taobao"

# 风格：id → (名称, 底图提示词片段)
STYLES: dict[str, tuple[str, str]] = {
    "minimal": ("简约高级", "简约高级质感，低饱和配色，柔和影棚光，大面积干净留白"),
    "fresh": ("清新自然", "清新自然风格，明亮自然光，浅色木质与绿植元素"),
    "tech": ("科技质感", "科技感，深色渐变背景，冷色轮廓光，金属与玻璃反射"),
    "warm": ("温馨生活", "温馨居家生活氛围，暖色调午后阳光，真实家居环境"),
    "bold": ("活力促销", "高饱和撞色背景，活力促销氛围，强对比光影"),
}
DEFAULT_STYLE = "minimal"

COUNTS = (4, 6, 8)
DEFAULT_COUNT = 6

# 生图像素（Seedream 要求总像素 ≥ 3686400）
GEN_SIZE = {"1:1": "1920x1920", "3:4": "1728x2304", "16:9": "2560x1440"}
GEN_SIZE_4K = {"1:1": "4096x4096", "3:4": "3072x4096", "16:9": "4096x2304"}
LANGUAGES = ("zh", "en", "ja", "none")
CLARITIES = ("2k", "4k")


@dataclass(frozen=True)
class Slot:
    """一种套图槽位：用途、画面模板、版式与画幅。"""

    key: str
    label: str
    prompt: str
    layout: str
    ratio: str
    blank: str
    is_main: bool = False


SLOTS: dict[str, Slot] = {
    "main_white": Slot(
        "main_white", "主图·白底",
        "电商主图，纯白无缝背景（RGB 255），商品居中占画面约 70%，柔和均匀的影棚光，地面有自然淡阴影",
        "none", "1:1", "none", is_main=True,
    ),
    "main_scene": Slot(
        "main_scene", "主图·场景",
        "电商主图，商品置于与品类相符的真实使用场景中，商品是视觉中心、占画面约 60%",
        "title_corner", "1:1", "top", is_main=True,
    ),
    "selling_1": Slot(
        "selling_1", "核心卖点",
        "电商卖点海报氛围图，商品突出，构图简洁有张力",
        "title_sub", "3:4", "top",
    ),
    "selling_2": Slot(
        "selling_2", "卖点详解",
        "电商卖点详解图，换一个拍摄角度展示商品，氛围与核心卖点图统一",
        "title_bullets", "3:4", "top",
    ),
    "selling_3": Slot(
        "selling_3", "卖点补充",
        "电商卖点补充图，突出商品的一个具体功能或使用效果",
        "title_sub", "3:4", "bottom",
    ),
    "detail": Slot(
        "detail", "细节特写",
        "商品局部微距特写，清晰展示材质、纹理与做工细节，浅景深",
        "label_bar", "3:4", "bottom",
    ),
    "usage": Slot(
        "usage", "使用场景",
        "真实使用情境图，可出现手部或人物局部正在使用商品，生活感强",
        "title_corner", "3:4", "top",
    ),
    "ad_banner": Slot(
        "ad_banner", "广告图·横版", "横版电商广告横幅，商品突出，宽幅简洁背景，适合品牌活动推广，不虚构优惠信息",
        "title_sub", "16:9", "top",
    ),
    "ad_square": Slot(
        "ad_square", "广告图·方形", "方形促销广告图，商品居中，醒目配色，突出真实卖点，不虚构价格与折扣",
        "title_bullets", "1:1", "top",
    ),
    "package": Slot(
        "package", "包装清单",
        "商品与包装盒、配件整齐平铺摆放的俯拍图，干净浅色背景",
        "title_sub", "3:4", "top",
    ),
}

# 各张数对应的槽位顺序
SET_COMPOSITION: dict[int, tuple[str, ...]] = {
    4: ("main_white", "main_scene", "selling_1", "usage"),
    6: ("main_white", "main_scene", "selling_1", "selling_2", "detail", "usage"),
    8: ("main_white", "main_scene", "selling_1", "selling_2", "selling_3", "detail", "usage", "package"),
}


# 规整平台 id，未知回落默认
def normalize_platform(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in PLATFORMS else DEFAULT_PLATFORM


# 规整风格 id，未知回落默认
def normalize_style(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in STYLES else DEFAULT_STYLE


# 规整张数，只允许 4/6/8
def normalize_count(value: int | str | None) -> int:
    try:
        n = int(value or DEFAULT_COUNT)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return n if n in COUNTS else DEFAULT_COUNT


# 给定张数返回槽位列表
def slots_for_count(count: int) -> list[Slot]:
    return [SLOTS[k] for k in SET_COMPOSITION[normalize_count(count)]]


# 自定义列表严格校验；未指定时沿用 4/6/8 预设。
def select_slots(count: int, keys: list[str] | None) -> list[Slot]:
    if keys is None:
        return slots_for_count(count)
    if not isinstance(keys, list) or not 3 <= len(keys) <= 10:
        raise ValueError("请选择 3 至 10 个槽位")
    if any(not isinstance(key, str) or key not in SLOTS for key in keys):
        raise ValueError("槽位不存在")
    if len(set(keys)) != len(keys):
        raise ValueError("槽位不能重复")
    slots = [SLOTS[key] for key in keys]
    if not any(slot.is_main for slot in slots):
        raise ValueError("请至少选择一个主图槽位")
    return slots


# 按清晰度选取尺寸：最长边不超过 4096，总像素不超过 4096²。
def generation_size(ratio: str, clarity: str = "2k") -> str:
    return (GEN_SIZE_4K if clarity == "4k" else GEN_SIZE)[ratio]


# 按平台与槽位取输出尺寸；4K 保留生图分辨率，广告槽位使用固定画幅。
def output_size(platform: str, slot_key: str, clarity: str = "2k") -> tuple[int, int]:
    _, main, detail = PLATFORMS[normalize_platform(platform)]
    slot = SLOTS.get(slot_key)
    if slot_key in {"ad_banner", "ad_square"}:
        size = (1600, 900) if slot_key == "ad_banner" else (1200, 1200)
    else:
        size = main if slot and slot.is_main else detail
    if clarity == "4k":
        ratio = "16:9" if slot_key == "ad_banner" else ("1:1" if size[0] == size[1] else "3:4")
        return tuple(map(int, generation_size(ratio, clarity).split("x")))
    return size


# 从实际输出尺寸取画幅，广告横图独立于平台主图规则。
def gen_ratio(platform: str, slot_key: str) -> str:
    w, h = output_size(platform, slot_key)
    return "16:9" if w > h else ("1:1" if w == h else "3:4")


# 槽位中文名
def slot_label(slot_key: str) -> str:
    slot = SLOTS.get(slot_key)
    return slot.label if slot else slot_key
