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
GEN_SIZE = {"1:1": "1920x1920", "3:4": "1728x2304"}


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
    # 底图参考：product 商品图 | model 固定模特 + 商品图（丝巾）| flat 不调模型、直接用商品图排版
    ref: str = "product"


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
    "package": Slot(
        "package", "包装清单",
        "商品与包装盒、配件整齐平铺摆放的俯拍图，干净浅色背景",
        "title_sub", "3:4", "top",
    ),
}

# 丝巾详情页槽位（参照真丝方巾店铺详情页的构成）
SCARF_SLOTS: dict[str, Slot] = {
    "scarf_cover": Slot(
        "scarf_cover", "封面·模特",
        "模特半身照，脸部清晰占画面较大，丝巾对折成三角披在双肩、在胸前打一个松结，一只手轻扶丝巾；人物偏右，画面左上留出竖排文字的干净空间",
        "vertical_title", "3:4", "none", is_main=True, ref="model",
    ),
    "scarf_hold": Slot(
        "scarf_hold", "整幅图案展示",
        "模特站在画面中央，双手捏住丝巾两角把整条丝巾完全展开平举在身前，完整展示丝巾的全部图案与四边，脸在丝巾上方，微笑看镜头",
        "label_bar", "3:4", "bottom", is_main=True, ref="model",
    ),
    "scarf_texture": Slot(
        "scarf_texture", "面料特写",
        "丝巾自然揉褶堆叠的微距特写，展示真丝缎面的光泽、垂坠与印花细节，柔和侧光，浅景深，下半部为丝巾，上部留出浅色留白",
        "title_bullets", "3:4", "top", ref="product",
    ),
    "scarf_knot": Slot(
        "scarf_knot", "颈前打结",
        "近景胸像，脸约占画面高度三分之一，丝巾折成三角在颈前打一个小结，两端自然垂落胸前，一只手轻捏结口",
        "label_bar", "3:4", "bottom", is_main=True, ref="model",
    ),
    "scarf_size": Slot(
        "scarf_size", "尺寸规格",
        "丝巾平铺图与尺寸标注",
        "size_spec", "3:4", "none", ref="flat",
    ),
    "scarf_back": Slot(
        "scarf_back", "背面披肩",
        "模特背对镜头、侧脸回望，丝巾展开成大三角披在肩背，三角尖垂到腰际，大面积图案在背部展开",
        "label_bar", "3:4", "bottom", ref="model",
    ),
    "scarf_closeup": Slot(
        "scarf_closeup", "近景特写",
        "近景特写，从头顶到锁骨，脸占画面高度约三分之一，微侧脸看向镜头，丝巾在颈侧打结、结口与花纹清晰",
        "none", "3:4", "none", ref="model",
    ),
    "scarf_long": Slot(
        "scarf_long", "长条领结",
        "模特半身，丝巾折成约 8 厘米宽的长条，在颈前打一个领结，两条长飘带垂到胸前，飘带上的图案清晰",
        "label_bar", "3:4", "bottom", ref="model",
    ),
    "scarf_street": Slot(
        "scarf_street", "街拍大衣",
        "街拍半身，模特穿驼色羊毛大衣敞开，丝巾折成长条挂在脖子上、两端自然垂在大衣前襟，走在洒满午后阳光的石板街道上，背景虚化",
        "label_bar", "3:4", "bottom", ref="model",
    ),
    "scarf_flat": Slot(
        "scarf_flat", "平铺原图",
        "丝巾平铺原图",
        "flat", "3:4", "none", ref="flat",
    ),
}
SLOTS.update(SCARF_SLOTS)

SCARF_COMPOSITION: dict[int, tuple[str, ...]] = {
    6: ("scarf_cover", "scarf_hold", "scarf_texture", "scarf_knot", "scarf_size", "scarf_back"),
    8: ("scarf_cover", "scarf_hold", "scarf_texture", "scarf_knot", "scarf_size", "scarf_closeup", "scarf_long",
        "scarf_back"),
    10: ("scarf_cover", "scarf_hold", "scarf_texture", "scarf_knot", "scarf_size", "scarf_closeup", "scarf_long",
         "scarf_back", "scarf_street", "scarf_flat"),
}
CATEGORIES = {"general": "通用商品", "scarf": "丝巾详情页"}


# 规整品类，未知回落通用
def normalize_category(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in CATEGORIES else "general"


# 按品类与张数返回槽位列表
def slots_for(category: str, count: int | str | None) -> list[Slot]:
    if normalize_category(category) == "scarf":
        from app.services.ecom_set.scarf import normalize_count as scarf_count

        return [SLOTS[k] for k in SCARF_COMPOSITION[scarf_count(count)]]
    return slots_for_count(normalize_count(count))


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


# 按平台与槽位得到最终输出像素 (宽, 高)
def output_size(platform: str, slot_key: str) -> tuple[int, int]:
    if slot_key.startswith("scarf_"):
        from app.services.ecom_set.scarf import OUTPUT_SIZE

        return OUTPUT_SIZE
    _, main, detail = PLATFORMS[normalize_platform(platform)]
    slot = SLOTS.get(slot_key)
    return main if slot and slot.is_main else detail


# 按输出尺寸选生图画幅（方图 1:1，其余 3:4），避免小红书等 3:4 主图被裁掉两侧
def gen_ratio(platform: str, slot_key: str) -> str:
    w, h = output_size(platform, slot_key)
    return "1:1" if w == h else "3:4"


# 槽位中文名
def slot_label(slot_key: str) -> str:
    slot = SLOTS.get(slot_key)
    return slot.label if slot else slot_key
