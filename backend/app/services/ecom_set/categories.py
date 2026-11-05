"""电商图「模特上身」品类注册表：丝巾 / 服饰 / 首饰 / 包袋。

每个品类给出：材质（质感 + 模特造型）、尺寸（可选）、槽位与默认张数构成、提示词拼装。
丝巾的细节规则在 scarf.py，这里统一对外提供查询函数，供套图规划、生成与前端选项使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.ecom_set import scarf as scarf_mod

# 输出画幅：id → (名称, 像素)
FRAMES: dict[str, tuple[str, tuple[int, int]]] = {
    "taobao_34": ("淘宝详情 3:4", (1440, 1920)),
    "main_11": ("主图 1:1", (1440, 1440)),
    "xhs_34": ("小红书 3:4", (1242, 1656)),
}
DEFAULT_FRAME = "taobao_34"


@dataclass(frozen=True)
class Category:
    """一个品类的生成规则。"""

    id: str
    name: str
    noun: str                                   # 封面里的品类词，如「真丝方巾」「连衣裙」
    materials: dict[str, tuple[str, str, str]]  # id → (名称, 质感描述, 模特造型)
    compositions: dict[int, tuple[str, ...]]    # 张数 → 槽位
    sizes: dict[str, str] = field(default_factory=dict)  # id → 名称（丝巾另有系法）
    default_material: str = ""
    default_count: int = 6
    product_word: str = "商品"


GENERIC_MODEL_PREFIX = (
    "参考图1是固定模特：画面中的人物必须是图1这位模特本人，脸型、五官、发型、肤色与气质完全一致，不要换人；"
    "参考图2是商品图，是商品款式、颜色、图案与细节的唯一来源。"
)
GENERIC_PRODUCT_PREFIX = "参考图是商品图，是商品款式、颜色、图案与细节的唯一来源。"
GENERIC_LOCK = (
    "商品的款式、版型、颜色、图案、五金与做工细节必须与商品参考图完全一致，不改款、不偏色、不增删部件；"
    "画面中不要出现任何文字、字幕、标语、价格、水印或 logo；商业时尚摄影，高清锐利，真实质感"
)
FABRIC_HINT = "最后一张参考图是面料/材质实拍：商品面料的织纹、光泽、厚薄与质感以它为准，只用在商品上，不参考其颜色和图案。"

APPAREL = Category(
    "apparel", "服饰", "服饰",
    materials={
        "silk": ("真丝", "真丝面料柔滑垂坠，带柔和珠光", "模特低盘发，妆容干净，搭配素色下装，浅米色墙面，柔和侧光"),
        "cashmere": ("羊绒", "羊绒面料细腻柔软，表面有细微绒感，哑光温暖", "模特低盘发，秋冬素色搭配，暖调室内窗边，柔和侧光"),
        "linen": ("棉麻", "棉麻面料自然肌理，透气挺括，微皱的自然感", "模特自然发型，清爽素色搭配，自然光室内或户外绿植背景"),
        "wool": ("羊毛呢", "羊毛呢面料厚实挺括，细密织纹，版型利落", "模特低盘发，秋冬通勤搭配，城市街景或浅色墙面"),
        "knit": ("针织", "针织面料纹理清晰，柔软有弹性", "模特自然发型，舒适素色搭配，暖调居家氛围"),
    },
    compositions={
        6: ("apparel_cover", "apparel_front", "apparel_back", "apparel_fabric", "apparel_craft", "apparel_street"),
        8: ("apparel_cover", "apparel_front", "apparel_side", "apparel_back", "apparel_fabric", "apparel_craft",
            "apparel_street", "apparel_flat"),
    },
    default_material="silk",
)
JEWELRY = Category(
    "jewelry", "首饰", "首饰",
    materials={
        "gold": ("黄金", "黄金质感温润，金属光泽细腻，边缘利落", "模特盘发露出颈部与耳朵，素色上衣，浅色背景，柔和光"),
        "silver": ("银饰", "银饰冷调金属光泽，抛光细腻", "模特盘发，素色上衣，浅灰背景，冷调柔光"),
        "pearl": ("珍珠", "珍珠圆润，有温润的珠光与晕彩", "模特低盘发，米白色上衣，暖调浅色背景，柔和光"),
        "jade": ("玉石", "玉石温润通透，色泽均匀", "模特低盘发，新中式素色上衣，浅米色背景，柔和光"),
    },
    compositions={
        6: ("jewelry_cover", "jewelry_wear", "jewelry_closeup", "jewelry_hero", "jewelry_macro", "jewelry_lifestyle"),
        8: ("jewelry_cover", "jewelry_wear", "jewelry_closeup", "jewelry_hand", "jewelry_hero", "jewelry_macro",
            "jewelry_lifestyle", "jewelry_flat"),
    },
    default_material="gold",
)
BAG = Category(
    "bag", "包袋", "包袋",
    materials={
        "cowhide": ("牛皮", "头层牛皮细腻纹理，柔和光泽，走线工整", "模特低盘发，简约通勤搭配，城市街景或浅色墙面"),
        "lambskin": ("羊皮", "羊皮柔软细腻，触感顺滑，哑光柔和", "模特低盘发，优雅素色搭配，浅米色墙面"),
        "canvas": ("帆布", "帆布织纹清晰，自然质朴", "模特自然发型，休闲搭配，户外自然光"),
        "woven": ("编织", "编织纹理清晰立体，手工感强", "模特自然发型，度假风搭配，阳光户外"),
    },
    compositions={
        6: ("bag_cover", "bag_shoulder", "bag_hand", "bag_hero", "bag_detail", "bag_street"),
        8: ("bag_cover", "bag_shoulder", "bag_hand", "bag_hero", "bag_side", "bag_detail", "bag_street", "bag_flat"),
    },
    default_material="cowhide",
)
GENERIC: dict[str, Category] = {c.id: c for c in (APPAREL, JEWELRY, BAG)}
STYLED_IDS = ("scarf", *GENERIC)


def is_styled(category: str | None) -> bool:
    """是否为「模特上身」类品类（含丝巾）。"""
    return (category or "") in STYLED_IDS


def normalize_frame(value: str | None) -> str:
    """规整画幅 id；旧数据里的平台 id 一律按淘宝 3:4。"""
    key = (value or "").strip()
    return key if key in FRAMES else DEFAULT_FRAME


def frame_size(value: str | None) -> tuple[int, int]:
    """画幅像素。"""
    return FRAMES[normalize_frame(value)][1]


def frame_ratio(value: str | None) -> str:
    """画幅对应的生图比例。"""
    w, h = frame_size(value)
    return "1:1" if w == h else "3:4"


def normalize_material(category: str, value: str | None) -> str:
    """规整材质。"""
    if category == "scarf":
        return scarf_mod.normalize_material(value)
    cat = GENERIC[category]
    key = (value or "").strip()
    return key if key in cat.materials else cat.default_material


def material_label(category: str, value: str | None) -> str:
    """材质名称。"""
    if category == "scarf":
        return scarf_mod.material_label(value)
    return GENERIC[category].materials[normalize_material(category, value)][0]


def normalize_size(category: str, value: str | None) -> str:
    """规整尺寸（只有丝巾按尺寸决定系法，其它品类原样保存）。"""
    if category == "scarf":
        return scarf_mod.normalize_size(value)
    return (value or "").strip()[:20]


def composition(category: str, count: int | str | None) -> tuple[str, ...]:
    """按张数给默认槽位。"""
    if category == "scarf":
        from app.services.ecom_set.presets import SCARF_COMPOSITION

        return SCARF_COMPOSITION[scarf_mod.normalize_count(count)]
    cat = GENERIC[category]
    try:
        n = int(count or cat.default_count)
    except (TypeError, ValueError):
        n = cat.default_count
    return cat.compositions.get(n) or cat.compositions[cat.default_count]


def counts(category: str) -> list[int]:
    """可选张数。"""
    return list(scarf_mod.COUNTS) if category == "scarf" else sorted(GENERIC[category].compositions)


def slot_keys(category: str) -> list[str]:
    """该品类全部可选槽位（按默认构成里的最大一组的顺序，再补其余）。"""
    from app.services.ecom_set.presets import SLOTS

    prefix = f"{category}_"
    longest = max((composition(category, n) for n in counts(category)), key=len)
    rest = [k for k in SLOTS if k.startswith(prefix) and k not in longest]
    return [*longest, *rest]


def build_prompt(category: str, *, slot_prompt: str, scene: str, ref: str, size: str | None,
                 material: str | None, has_fabric: bool) -> str:
    """拼装底图提示词。"""
    if category == "scarf":
        text = scarf_mod.build_prompt(slot_prompt=slot_prompt, scene=scene, ref=ref, size=size, material=material)
    else:
        cat = GENERIC[category]
        _, texture, look = cat.materials[normalize_material(category, material)]
        prefix = GENERIC_MODEL_PREFIX if ref == "model" else GENERIC_PRODUCT_PREFIX
        parts = [prefix, slot_prompt, scene, look if ref == "model" else "", f"材质：{texture}", GENERIC_LOCK]
        text = "。".join(p.strip("。 ") for p in parts if p and p.strip())
    return f"{text}。{FABRIC_HINT}" if has_fabric else text


def plan_notes(category: str, *, size: str | None, material: str | None, notes: str) -> str:
    """给规划模型的品类补充信息。"""
    if category == "scarf":
        return scarf_mod.plan_notes(size, notes, material)
    cat = GENERIC[category]
    _, texture, _ = cat.materials[normalize_material(category, material)]
    head = f"品类：{cat.name}；材质：{material_label(category, material)}（{texture}）"
    if size:
        head += f"；规格：{size}"
    return f"{head}。{notes.strip()}" if notes.strip() else head


def cover_copy(category: str, *, name: str, size: str | None, material: str | None) -> dict:
    """封面竖排文案。"""
    if category == "scarf":
        return scarf_mod.cover_copy(name, size, material)
    base = (name or "").strip("《》 ")
    return {"title": base[:12], "subtitle": f"{material_label(category, material)} {GENERIC[category].noun}"[:16],
            "bullets": []}


def options() -> list[dict]:
    """前端选项：每个品类的材质、尺寸、张数与槽位。"""
    from app.services.ecom_set.presets import SLOTS

    out = []
    for cid in STYLED_IDS:
        if cid == "scarf":
            mats = [{"id": k, "name": v[0]} for k, v in scarf_mod.MATERIALS.items()]
            sizes = [{"id": k, "name": v[0]} for k, v in scarf_mod.SIZES.items()]
            name, default_material, default_size = "丝巾", scarf_mod.DEFAULT_MATERIAL, scarf_mod.DEFAULT_SIZE
            default_count = scarf_mod.DEFAULT_COUNT
        else:
            cat = GENERIC[cid]
            mats = [{"id": k, "name": v[0]} for k, v in cat.materials.items()]
            sizes, name, default_material, default_size = [], cat.name, cat.default_material, ""
            default_count = cat.default_count
        out.append({
            "id": cid, "name": name, "materials": mats, "sizes": sizes,
            "default_material": default_material, "default_size": default_size,
            "counts": counts(cid), "default_count": default_count,
            "compositions": {str(n): list(composition(cid, n)) for n in counts(cid)},
            "slots": [{"key": k, "label": SLOTS[k].label, "ref": SLOTS[k].ref, "free": SLOTS[k].ref == "flat"}
                      for k in slot_keys(cid)],
        })
    return out
