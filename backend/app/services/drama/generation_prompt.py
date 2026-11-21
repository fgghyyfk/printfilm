"""漫剧资产生图提示词拼接（角色/场景结构前缀 + 内置风格）。

与 manju generationPrompt.ts 对齐：character/scene 加结构强制前缀。
ai_movie 扩展：prop/material 增加静物/空镜前缀（manju 无此前缀）。
风格不写进结构前缀，由 append_style_prompt 追加项目风格。
"""

from __future__ import annotations

from app.services.drama.character_form import is_non_human_character
from app.services.drama.image_styles import STYLE_BOARD_PROMPT_HINT, resolve_image_style_prompt

# CHARACTER_PROMPT_PREFIX 角色设定板结构要求（无风格词）
CHARACTER_PROMPT_PREFIX = (
    "【强制任务：角色设定板构图】"
    "纯白背景，无复杂背景、无遮挡、无文字水印。"
    "画面左侧为同一角色的全身三视图，占据主要视觉区域，从左到右固定为："
    "1.正面全身站姿；2.左侧面全身站姿；3.背面全身站姿。"
    "三人必须是同一角色：五官、发型、服装、体型、身高比例完全一致。"
    "站姿自然，正面站立，双臂自然下垂，无其他动作，无夸张表情，全身完整入镜（从头到脚）。"
    "同图还必须同时包含：①面部大头特写（五官、肤色、妆容与发际线清晰，可做人脸参考）；"
    "②半身像特写（头肩至腰部，服饰上半与体态清晰）。"
    "镜头平视，中性棚拍光，无夸张透视。"
    "人物边缘清晰，服装版型明确，整体留白充足，类似角色建模参考页/设定板排版。"
    "严禁：只出单张全身或缺三视图；缺面部特写或缺半身像；风景/道具为主体；复杂背景。"
    "即使用户描述偏向场景、物品或动作片段，也必须转化为可辨识的白底角色设定板来呈现，"
    "并据此补全外貌、体态与服饰。请严格依据以下用户描述生成上述结构的角色设定图："
)

# CHARACTER_BODY_MARKER 角色设定板前缀的结束标记（人物/非人两套共用，之后为用户正文）
CHARACTER_BODY_MARKER = "请严格依据以下用户描述生成上述结构的角色设定图："

# NON_HUMAN_CHARACTER_PROMPT_PREFIX 非人角色（会说话的纸鹤、动物、器物等）设定板：保留三视图，禁止画成人
NON_HUMAN_CHARACTER_PROMPT_PREFIX = (
    "【强制任务：非人角色设定板构图】"
    "纯白背景，无复杂背景、无遮挡、无文字水印。"
    "本角色不是人类：必须按用户描述的本体形态（动物、物件、器物、植物或精灵等）呈现，"
    "严禁画成人类、人形或拟人化人物，严禁出现任何人物、人手或手持者。"
    "画面左侧为同一角色本体的完整三视图，占据主要视觉区域，从左到右固定为："
    "1.正面完整视图；2.左侧面完整视图；3.背面完整视图。"
    "三者必须是同一角色：外形轮廓、材质、颜色、比例与标志性细节完全一致。"
    "同图还必须同时包含：①标志性部位特写（眼睛、纹样、折痕、光效等可辨识细节）；"
    "②神态/姿态特写（体现性格与动态感）。"
    "镜头平视，中性棚拍光，无夸张透视。"
    "本体边缘清晰，比例明确，整体留白充足，类似角色建模参考页/设定板排版。"
    "严禁：画成人或人形；出现人物或人手；缺三视图；风景为主体；复杂背景。"
    + CHARACTER_BODY_MARKER
)


# SCENE_PROMPT_PREFIX 场景设计参考图结构要求（无风格词）
SCENE_PROMPT_PREFIX = (
    "【强制任务：场景设计参考图构图】"
    "单张图内同时包含平视视角与俯视视角。"
    "画面左侧：围合空间的多面立面设计（含墙体展开），须体现门洞/门及其他装修结构。"
    "画面右侧：2～4 个功能区域的细节展开（材质、家具或空间局部清晰可辨）。"
    "以环境空间与建筑结构为主体，严禁人物特写、角色立绘或以人物为视觉中心。"
    "即使用户描述涉及人物、角色或动作，也必须剥离人物主体，仅保留可独立成立的环境与结构信息。"
    "请严格依据以下用户描述生成上述结构的场景设计图："
)


# PROP_PROMPT_PREFIX 道具设定板结构要求（无风格词）
PROP_PROMPT_PREFIX = (
    "【强制任务：道具设定板构图】"
    "纯白背景，无复杂背景、无遮挡、无文字水印。"
    "画面左侧为同一道具的完整三视图，占据主要视觉区域，从左到右固定为："
    "1.正面完整视图；2.左侧面完整视图；3.背面完整视图。"
    "三件必须是同一道具：外形轮廓、材质分区、颜色、比例、细节完全一致。"
    "物件完整入镜，摆放平稳，无手持人物、无使用动作、无夸张透视。"
    "同图还必须同时包含：①关键局部特写（铭文、机关、接口、纹样或破损等可辨识细节）；"
    "②材质/结构半身级特写（表面质感、拼接与厚度清晰）。"
    "镜头平视，中性棚拍光；物件边缘清晰，形制与比例明确，整体留白充足，类似道具建模参考页/产品设定板排版。"
    "严禁：只出单角度或缺三视图；缺局部特写；人物立绘/肖像；风景场景为主体；复杂背景。"
    "即使用户描述涉及人物使用或剧情动作，也必须剥离人物，仅保留可独立成立的道具本体。"
    "请严格依据以下用户描述生成上述结构的道具设定图："
)

# MATERIAL_PROMPT_PREFIX 气氛素材强制前缀
MATERIAL_PROMPT_PREFIX = (
    "【强制任务：生成气氛空镜素材】本次必须生成环境/气氛静帧，"
    "强调构图、光影与氛围，可作为短剧空镜参考。"
    "严禁生成可识别人脸特写或角色立绘。"
    "请严格依据以下用户描述生成素材画面："
)

# IMAGE_PROMPT_PREFIX 自由画布图片：短意图也必须钉住主体
IMAGE_PROMPT_PREFIX = (
    "【强制任务：按用户意图生成单张画面】"
    "用户描述中的主体必须是画面绝对中心，禁止替换成无关物体，"
    "禁止白底多视图设定板，禁止只出空镜氛围而丢掉主体。"
    "请严格依据以下用户描述生成画面："
)


# 将内置风格提示词追加到正文后；设定板类资产强调构图优先于风格场景
# has_style_board 为真时补一句：只借画风板气质，禁止抄主体
def append_style_prompt(
    prompt: str,
    style_id: str | None = None,
    *,
    structure_locked: bool = False,
    has_style_board: bool = False,
) -> str:
    style_prompt = resolve_image_style_prompt(style_id)
    if not style_prompt:
        return prompt
    board_hint = f"。{STYLE_BOARD_PROMPT_HINT}" if has_style_board else ""
    if structure_locked:
        return (
            f"{prompt}。外观材质与画风参考：{style_prompt}{board_hint}"
            "（须服从上文白底设定板构图与排版，禁止改成复杂场景或替换纯白背景）"
        )
    return f"{prompt}。画面风格要求：{style_prompt}{board_hint}"


_STRUCTURE_PREFIX_BY_KIND: dict[str, str] = {
    "character": CHARACTER_PROMPT_PREFIX,
    "scene": SCENE_PROMPT_PREFIX,
    "prop": PROP_PROMPT_PREFIX,
    "material": MATERIAL_PROMPT_PREFIX,
    "none": MATERIAL_PROMPT_PREFIX,
    "image": IMAGE_PROMPT_PREFIX,
}

# 提示词正文是否已含设定板/构图强制任务（避免重复拼接）
def prompt_has_structure_prefix(prompt: str) -> bool:
    text = prompt or ""
    return "【强制任务：" in text or "全身三视图" in text or "完整三视图" in text


# 将三视图等结构前缀写入提示词正文（供提示词框展示；提交时不再静默携带）
def ensure_structure_in_prompt(user_prompt: str, asset_type: str | None = None) -> str:
    trimmed = (user_prompt or "").strip()
    if not trimmed:
        return trimmed
    kind = (asset_type or "").strip().lower()
    if prompt_has_structure_prefix(trimmed):
        return fix_character_structure_prefix(trimmed) if kind == "character" else trimmed
    if kind == "character":
        return f"{character_prompt_prefix(trimmed)}{trimmed}"
    prefix = _STRUCTURE_PREFIX_BY_KIND.get(kind)
    if not prefix:
        return trimmed
    return f"{prefix}{trimmed}"


# 按角色正文选人物/非人设定板前缀
def character_prompt_prefix(body: str) -> str:
    return NON_HUMAN_CHARACTER_PROMPT_PREFIX if is_non_human_character(body) else CHARACTER_PROMPT_PREFIX


# 已带人物设定板前缀、但正文是非人本体时换成非人前缀（修正旧资产，正文原样保留）
def fix_character_structure_prefix(prompt: str) -> str:
    text = prompt or ""
    if not text.startswith(CHARACTER_PROMPT_PREFIX):
        return text
    body = text[len(CHARACTER_PROMPT_PREFIX):]
    if not is_non_human_character(body):
        return text
    return f"{NON_HUMAN_CHARACTER_PROMPT_PREFIX}{body}"


# 按资产类型与风格 ID 组装 Seedream 提交提示词（结构前缀须已在正文中，此处只追加风格）
def build_generation_prompt(
    user_prompt: str,
    asset_type: str | None = None,
    style_id: str | None = None,
    *,
    has_style_board: bool = False,
) -> str:
    trimmed = (user_prompt or "").strip()
    kind = (asset_type or "").strip().lower()
    if kind == "character":
        trimmed = fix_character_structure_prefix(trimmed)
    structure_locked = kind in {"character", "prop"} and prompt_has_structure_prefix(trimmed)
    out = append_style_prompt(
        trimmed, style_id, structure_locked=structure_locked, has_style_board=has_style_board
    )
    if out != trimmed and trimmed.startswith(NON_HUMAN_CHARACTER_PROMPT_PREFIX):
        # 画风词里常有「人体比例、五官、服饰」，非人角色只借笔触与色彩
        out += "（本角色为非人本体，画风只取笔触、色彩与光影，不得据此画出人物或人形）"
    return out
