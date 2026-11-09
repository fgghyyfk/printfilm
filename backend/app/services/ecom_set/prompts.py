"""一图出套图：规划 system prompt、底图提示词拼装、广告法极限词过滤。"""

from __future__ import annotations

import re

from app.services.compliance.ad_words import Hit, check_text
from app.services.ecom_set.presets import STYLES, Slot, normalize_style

# 文案长度上限（字）
TITLE_MAX = 10
SUBTITLE_MAX = 16
BULLET_MAX = 12
BULLETS_MAX = 3

# 底图统一后缀：锁定商品外观、禁止画面文字
LOCK_SUFFIX = (
    "保持参考图中商品的外形、比例、颜色、logo 与包装文字完全一致，不增删部件，只改变背景、环境与光影；"
    "画面中不要出现任何额外文字、字幕、标语、价格、水印；不要添加任何其他品牌的 logo、商标或商品标签；"
    "商业摄影，高清锐利，真实质感"
)
# 生图反向提示词：禁画面文字、广告语与他牌标识
NEGATIVE_PROMPT = (
    "文字，汉字，英文字母，数字，标语，广告语，促销字样，价格标签，印章，水印，"
    "其他品牌 logo，商标，新增商品标签，logo 变形，商品变形，多余部件，低清，模糊，畸变"
)
BLANK_HINT = {
    "top": "画面上方约三分之一保留干净、简洁的留白区域用于后期排版文字，商品位于画面中下部",
    "bottom": "画面下方约四分之一保留干净、简洁的留白区域用于后期排版文字，商品位于画面中上部",
    "none": "",
}

PLAN_SYSTEM = """你是资深电商视觉策划与文案。根据商品图片（如有）和用户提供的信息，为一套电商图做出图方案。
严格只输出一个 JSON 对象，不要任何解释或 Markdown，结构如下：
{
  "product": {"name": "品名", "category": "品类", "colors": ["主色"], "selling_points": ["卖点1", "卖点2", "卖点3"]},
  "items": [
    {"slot": "槽位key（与输入一致，顺序一致）", "scene": "底图画面描述", "title": "主标题", "subtitle": "副标题", "bullets": ["要点"]}
  ]
}
要求：
1. items 数量和 slot 必须与输入的槽位列表完全一致、顺序一致。
2. scene 用 40-80 字中文描述该张底图的场景、道具、光线、机位；必须围绕这个商品真实的用途，不要编造商品不具备的功能；scene 里不要写任何要出现在画面上的文字。
3. title ≤10 字、subtitle ≤16 字、每条 bullet ≤12 字、bullets 最多 3 条；标注为「无文案」的槽位 title/subtitle/bullets 留空。
4. 文案口语、具体、有画面感，突出用户利益；不同图片的标题不要重复。
5. 严禁使用《广告法》极限词：最、第一、唯一、顶级、国家级、极致、绝对、100%、全网、永久、万能 等；不得虚构认证、销量、功效数据。
6. 如果看不到图片，就只根据用户给的品名和卖点来规划，不要假装看到了图片。"""


# 组装规划的用户消息文本
def build_plan_user_text(
    *, name: str, notes: str, platform_name: str, style: str, slots: list[Slot]
) -> str:
    style_name, _ = STYLES[normalize_style(style)]
    lines = [
        f"品名：{name}",
        f"投放平台：{platform_name}",
        f"整体风格：{style_name}",
    ]
    if notes.strip():
        lines.append(f"用户补充的卖点/信息：{notes.strip()}")
    lines.append("槽位列表（按顺序）：")
    for i, slot in enumerate(slots, 1):
        copy_hint = "无文案" if slot.layout == "none" else f"版式 {slot.layout}"
        lines.append(f"{i}. slot={slot.key}（{slot.label}，{copy_hint}）用途：{slot.prompt}")
    return "\n".join(lines)


# 拼装一张底图的生图提示词（画面描述再过一遍极限词兜底，防止上游漏网）
def build_scene_prompt(*, slot: Slot, scene: str, style: str, blank: str) -> str:
    _, style_prompt = STYLES[normalize_style(style)]
    clean_scene, _ = sanitize_scene(scene)
    parts = [slot.prompt, clean_scene, style_prompt, BLANK_HINT.get(blank, ""), LOCK_SUFFIX]
    return "。".join(p.strip("。 ") for p in parts if p and p.strip())


# 画面描述清洗：去掉引号里要「写在画面上」的字，再过滤极限词，返回 (新文本, 命中词)
def sanitize_scene(text: str) -> tuple[str, list[Hit]]:
    raw = re.sub(r"[「“\"『《][^」”\"』》]{0,40}[」”\"』》]", "", text or "")
    raw = re.sub(r"(写着|印有|标有|配文|文字为)[^，。；,;]*", "", raw)
    return sanitize_copy(raw)


# 文案过极限词库，返回 (新文本, 命中列表)
def sanitize_copy(text: str) -> tuple[str, list[Hit]]:
    return check_text(text)


# 截断到最大字数
def clip(text: str, limit: int) -> str:
    s = (text or "").strip()
    return s[:limit]


# 规整一组文案：截断 + 过滤极限词，返回 (copy dict, 命中词)
def title_limit(slot_key: str) -> int:
    """某张图的主标题字数上限：日化封面版式会自动换行，放宽到 16 字。"""
    return 16 if slot_key == "goods_cover" else TITLE_MAX


def normalize_copy(raw: dict | None, *, layout: str, title_max: int = TITLE_MAX) -> tuple[dict, list[Hit]]:
    if layout == "none":
        return {"title": "", "subtitle": "", "bullets": []}, []
    body = raw if isinstance(raw, dict) else {}
    hits: list[Hit] = []
    title, h1 = sanitize_copy(str(body.get("title") or ""))
    subtitle, h2 = sanitize_copy(str(body.get("subtitle") or ""))
    hits += h1 + h2
    bullets: list[str] = []
    for b in list(body.get("bullets") or [])[:BULLETS_MAX]:
        text, h = sanitize_copy(str(b or ""))
        hits += h
        if text:
            bullets.append(clip(text, BULLET_MAX))
    return {
        "title": clip(title, title_max),
        "subtitle": clip(subtitle, SUBTITLE_MAX),
        "bullets": bullets,
    }, hits
