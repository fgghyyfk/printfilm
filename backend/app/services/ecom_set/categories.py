"""电商图「模特上身」品类注册表：丝巾 / 服饰 / 首饰 / 包袋。

每个品类给出：材质（质感 + 模特造型）、尺寸（可选）、槽位与默认张数构成、提示词拼装。
丝巾的细节规则在 scarf.py，这里统一对外提供查询函数，供套图规划、生成与前端选项使用。
"""

from __future__ import annotations

import re
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
# 日化 / 标品：不用模特，商品用品牌抠图原样合成进场景；「材质」改作包装形态（决定摆放方式与比例）
GOODS = Category(
    "goods", "日化/标品", "",
    materials={
        "tube": ("软管", "哑光软管包装，管盖朝下竖立摆放", ""),
        "bottle": ("瓶装", "瓶装（泵头或旋盖），竖立摆放", ""),
        "jar": ("罐装", "圆罐包装，平放在台面上", ""),
        "box": ("盒装", "盒装包装，正面朝向镜头摆放", ""),
        "pouch": ("袋装", "袋装包装，正面朝向镜头立放", ""),
    },
    compositions={
        # 第二张直接放卖点（不用白底）；白底图放第 5 张（淘宝主图第 5 张常要求白底）
        6: ("goods_cover", "goods_selling", "goods_scene", "goods_texture", "goods_white", "goods_usage"),
        8: ("goods_cover", "goods_selling", "goods_scene", "goods_ingredient_still", "goods_white", "goods_texture",
            "goods_flatlay", "goods_usage"),
        # 10～14 张：加信息屏（免费，按商品资料排版；资料不够的那张会自动跳过并提示）、成分图与更多构图
        10: ("goods_cover", "goods_selling", "goods_scene", "goods_info_points", "goods_white", "goods_ingredient_still",
             "goods_texture", "goods_ingredients_photo", "goods_usage", "goods_info_specs"),
        12: ("goods_cover", "goods_selling", "goods_pain", "goods_scene", "goods_white", "goods_info_points",
             "goods_ingredient_still", "goods_texture", "goods_ingredients_photo", "goods_hand", "goods_info_usage",
             "goods_info_specs"),
        14: ("goods_cover", "goods_selling", "goods_pain", "goods_scene", "goods_white", "goods_info_points",
             "goods_ingredient_still", "goods_info_variants", "goods_lineup", "goods_texture", "goods_ingredients_photo",
             "goods_hand", "goods_usage", "goods_info_specs"),
    },
    default_material="tube",
)
# 电动工具 / 五金：同样走「抠图原样合成」，保证铭牌、参数、logo 不被 AI 重画；「材质」改作工具形态
TOOL = Category(
    "tool", "电动工具/五金", "",
    materials={
        "pistol": ("手枪式", "手枪式电动工具（电钻、起子机、扳手），握把朝下、机身水平，侧面朝向镜头立放", ""),
        "straight": ("直柄式", "直柄式工具（角磨机、打磨机、热风枪），机身横放，侧面朝向镜头", ""),
        "hand": ("手动工具", "手动五金工具（扳手、钳子、螺丝刀），平放或斜靠摆放", ""),
        "kit": ("套装箱", "工具套装箱，正面朝向镜头摆放", ""),
    },
    compositions={
        6: ("tool_cover", "tool_power", "tool_scene", "tool_detail", "goods_white", "tool_usage"),
        8: ("tool_cover", "tool_power", "tool_scene", "tool_detail", "goods_white", "tool_usage", "tool_workbench",
            "goods_info_specs"),
        10: ("tool_cover", "tool_power", "tool_pain", "tool_scene", "goods_white", "goods_info_points", "tool_detail",
             "tool_usage", "tool_workbench", "goods_info_specs"),
        12: ("tool_cover", "tool_power", "tool_pain", "tool_scene", "goods_white", "goods_info_points", "tool_detail",
             "tool_hand", "tool_usage", "tool_workbench", "goods_info_usage", "goods_info_specs"),
    },
    default_material="pistol",
    default_count=8,
)
GENERIC: dict[str, Category] = {c.id: c for c in (APPAREL, JEWELRY, BAG, GOODS, TOOL)}
# 走「抠图原样合成」的品类：不用模特，商品抠图贴回场景
COMPOSITE_IDS = frozenset({"goods", "tool"})
STYLED_IDS = ("scarf", *GENERIC)


def is_composite(category: str | None) -> bool:
    """是否为「抠图原样合成」类品类（日化标品、电动工具）。"""
    return (category or "") in COMPOSITE_IDS


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


# 标品合成：参考图是已经按比例摆好的商品
COMPOSITE_PREFIX = (
    "以参考图中已经摆放好的商品为主体，严格保持每件商品的位置、大小、形状、颜色和包装上的文字完全不变，"
    "不要改动、不要新增或复制任何商品；只在商品周围搭建背景、台面、道具与光影；"
    "商品保持参考图里的原有材质（哑光包装就是哑光），不要给商品加镜面高光、塑料反光或光滑的渐变光泽"
)
COMPOSITE_LOCK = "商业静物摄影，真实质感，浅景深；画面中不要出现任何额外文字、字母、logo、水印；不要出现其他品牌的商品"
TEXTURE_PREFIX = "画面中不要出现任何包装或商品容器，只表现商品内容物本身"
HAND_PREFIX = ("参考图是商品：画一只手握持这件商品，商品的外形、颜色、比例与包装上的文字、logo 必须与参考图完全一致，"
               "不要改字、不要新增文字；包装质感按真实实物：哑光、细腻的印刷纸面或磨砂软管质感，"
               "不要画成光滑反光的塑料或 3D 渲染感")
TOOL_ORIENT = "工具的朝向、机头方向与参考图完全一致，不要左右镜像翻转，不要转动角度，机身上的铭牌与文字保持原样"
TOOL_HAND_PREFIX = ("参考图是商品：画一只成年人的手稳稳握住这件工具的握把、食指搭在扳机上，"
                    "工具的外形、配色、比例与机身上的铭牌、logo、文字必须与参考图完全一致，不要改字、不要新增文字；"
                    "机身是哑光工程塑料与橡胶握把的真实质感，不要画成光滑反光的玩具或 3D 渲染感；手部比例真实，手指数量正确；"
                    "工具朝向与参考图完全一致、不要左右镜像：参考图里朝向镜头的是哪一侧（如铭牌一侧），画面里也必须是这一侧朝向镜头，"
                    "机头/夹头指向与参考图相同的方向，作业对象放在机头所指的那一侧")
BLANK_HINT = {"top": "画面上方约 30% 是干净的背景留白，用于排版文字", "bottom": "画面下方约 25% 是干净的留白，用于排版文字"}


def scene_of(info: dict | None, *, with_props: bool = True) -> str:
    """从套图信息里取场景卡描述（没选场景时为空）。选了元素时不带场景卡道具（道具由每张的元素决定）。"""
    from app.services.ecom_set.scenes import scene_text

    scene = (info or {}).get("scene") or {}
    return scene_text(scene.get("spec") or {}, with_props=with_props) if isinstance(scene, dict) else ""


REAL_HINT = ("商品设计图后面的一张是实物照片：商品在真实光线下的颜色、质感、厚薄与垂坠以它为准；"
             "图案和细节仍以设计图为准。")


def build_prompt(category: str, *, slot_prompt: str, scene: str, ref: str, size: str | None,
                 material: str | None, has_fabric: bool, has_real: bool = False, scene_card: str = "",
                 blank: str = "none", shot: str = "", elements: str = "", dims: dict | None = None) -> str:
    """拼装底图提示词。scene 为本张画面描述；场景卡描述由 scene_card 传入（标品与纯商品槽位使用）。

    dims：商品实际尺寸（厘米），有时追加比例约束，让道具、手、模特与商品按真实大小搭配。
    """
    from app.services.ecom_set.dims import scale_note

    scale = scale_note(dims, ref=ref) if category != "scarf" else ""
    if category in COMPOSITE_IDS:
        text = build_goods_prompt(slot_prompt=slot_prompt, scene=scene, ref=ref, scene_card=scene_card,
                                  blank=blank, material=material, shot=shot, elements=elements, category=category)
        return f"{text}。{scale}" if scale else text
    if elements and ref == "product":
        scene_card = "。".join(x for x in (scene_card, elements) if x)
    if category == "scarf":
        text = scarf_mod.build_prompt(slot_prompt=slot_prompt, scene=scene, ref=ref, size=size, material=material)
    else:
        cat = GENERIC[category]
        _, texture, look = cat.materials[normalize_material(category, material)]
        prefix = GENERIC_MODEL_PREFIX if ref == "model" else GENERIC_PRODUCT_PREFIX
        # 纯商品槽位跟随场景卡；模特槽位沿用各自的拍摄地点
        card = scene_card if ref == "product" else ""
        parts = [prefix, slot_prompt, card, scene, look if ref == "model" else "", f"材质：{texture}", GENERIC_LOCK]
        text = "。".join(p.strip("。 ") for p in parts if p and p.strip())
    if scale:
        text = f"{text}。{scale}"
    if has_real:
        text = f"{text}。{REAL_HINT}"
    return f"{text}。{FABRIC_HINT}" if has_fabric else text


def build_goods_prompt(*, slot_prompt: str, scene: str, ref: str, scene_card: str, blank: str,
                       material: str | None, shot: str = "", elements: str = "", category: str = "goods") -> str:
    """标品底图提示词：合成槽位围绕已摆好的商品搭景；质地槽位只拍内容物；手持槽位按商品参考图画手握商品。

    shot：本张镜头构图描述；elements：本张画面元素（来自元素库，每张不同）；category：goods / tool。
    """
    if ref == "texture":
        parts = [TEXTURE_PREFIX, slot_prompt, scene, scene_card, elements, BLANK_HINT.get(blank, ""), COMPOSITE_LOCK]
    elif ref == "hand":
        prefix = TOOL_HAND_PREFIX if category == "tool" else HAND_PREFIX
        parts = [prefix, slot_prompt, scene_card, elements, scene, BLANK_HINT.get(blank, ""), COMPOSITE_LOCK]
    else:
        cat = GENERIC[category if category in COMPOSITE_IDS else "goods"]
        _, form, _ = cat.materials[normalize_material(cat.id, material)]
        lay = "；本张商品平放在台面上" if "平放" in shot else ""
        orient = TOOL_ORIENT if category == "tool" else ""
        parts = [COMPOSITE_PREFIX, orient, f"商品摆放：{form}{lay}", slot_prompt, shot, scene_card, elements, scene,
                 BLANK_HINT.get(blank, ""), COMPOSITE_LOCK]
    return "。".join(p.strip("。 ") for p in parts if p and p.strip())


def plan_notes(category: str, *, size: str | None, material: str | None, notes: str) -> str:
    """给规划模型的品类补充信息。"""
    if category == "scarf":
        return scarf_mod.plan_notes(size, notes, material)
    cat = GENERIC[category]
    _, texture, _ = cat.materials[normalize_material(category, material)]
    kind = {"goods": "包装", "tool": "形态"}.get(category, "材质")
    head = f"品类：{cat.name}；{kind}：{material_label(category, material)}（{texture}）"
    if size:
        head += f"；规格：{size}"
    return f"{head}。{notes.strip()}" if notes.strip() else head


def cover_copy(category: str, *, name: str, size: str | None, material: str | None) -> dict:
    """封面竖排文案。"""
    if category == "scarf":
        return scarf_mod.cover_copy(name, size, material)
    base = (name or "").strip("《》 ")
    if category in COMPOSITE_IDS:
        return goods_cover_copy(base, size)
    noun = GENERIC[category].noun
    sub = f"{material_label(category, material)} {noun}".strip() if noun else ""
    return {"title": base[:12], "subtitle": sub[:16], "bullets": []}


GOODS_SPEC_RE = re.compile(r"\s*[\d.]+\s*(g|kg|ml|l|克|千克|毫升|升|片|支|粒|袋|瓶)\s*$", re.I)
GOODS_TITLE_MAX = 16


def goods_cover_copy(name: str, size: str | None) -> dict:
    """日化/标品封面：标题用商品名（去掉末尾规格，最多 16 字，版式会自动换行），规格放副标题。"""
    m = GOODS_SPEC_RE.search(name)
    title = name[: m.start()].strip() if m and m.start() > 0 else name
    spec = (size or "").strip() or (m.group(0).strip() if m else "")
    if len(title) > GOODS_TITLE_MAX:
        cut = title[:GOODS_TITLE_MAX]
        # 尽量在空格处断开，避免把英文单词或中文词截成半个
        title = cut[: cut.rfind(" ")].strip() if " " in cut[GOODS_TITLE_MAX // 2:] else cut
    return {"title": title, "subtitle": spec[:16], "bullets": []}


def options() -> list[dict]:
    """前端选项：每个品类的材质、尺寸、张数与槽位。"""
    from app.services.ecom_set.presets import FREE_REFS, SLOTS

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
            "slots": [{"key": k, "label": SLOTS[k].label, "ref": SLOTS[k].ref, "free": SLOTS[k].ref in FREE_REFS}
                      for k in slot_keys(cid)],
            "uses_model": any(SLOTS[k].ref == "model" for k in slot_keys(cid)),
            "material_label": {"goods": "包装形态", "tool": "工具形态"}.get(cid, "材质"),
            "composite": cid in COMPOSITE_IDS,
        })
    return out
