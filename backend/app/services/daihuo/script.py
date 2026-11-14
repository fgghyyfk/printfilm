"""商品带货：按套图写分段带货口播（每段对应一张套图），并做极限词替换。"""

from __future__ import annotations

from typing import Any

from app.services.compliance.changes import clean_fields
from app.services.ecom_set.planner import extract_json
from app.services.ecom_set.prompts import clip

# 每段口播字数（约 4 字/秒）
SEG_MIN_CHARS = 14
SEG_MAX_CHARS = 48

DAIHUO_SYSTEM = """你是抖音/小红书带货短视频的口播编导。根据商品信息和一组商品图（每张图的用途与卖点），
写一条竖屏带货口播，每张图对应一段口播。严格只输出一个 JSON 对象：
{"title": "视频标题（≤16 字）", "segments": [{"idx": 图片序号, "title": "这段画面上的大字（≤10 字）", "narration": "这段口播"}]}
要求：
1. segments 必须覆盖输入的每一张图，idx 一一对应、顺序一致，不要增删。
2. 第一段是 3 秒钩子（痛点/反差/场景提问），最后一段是行动号召（如「点下方链接看看」），不承诺效果。
3. 每段口播 14–48 字，口语短句，一口气能读完；只讲图里这张图对应的卖点，不同段不要重复同一句话。
4. 只使用给定的商品信息与卖点，不编造价格、销量、认证、功效数据；不点名其他品牌。
5. 严禁广告法极限词：最、第一、唯一、顶级、国家级、极致、绝对、100%、全网、永久、万能 等。"""


# 动态展示附加要求：每段多给动作建议，动作只能取白名单 key
def motion_addon() -> str:
    from app.services.daihuo.motion import MOTION_ACTIONS

    keys = "、".join(f"{k}（{v[0]}）" for k, v in MOTION_ACTIONS.items())
    return (
        "\n6. 这条视频部分画面会做成动态镜头。每段额外输出三个字段："
        "\"motion\": 这张图是否适合做动态（商品主体清晰、做动作有看点时为 true）；"
        f"\"action\": 只能从 {keys} 中选一个 key；"
        "\"video\": 一句不超过 40 字的画面动作补充（只描述商品与镜头，不出现人物、文字和其他品牌）。"
        "使用演示 demo 只在商品本身支持该动作时才选。"
    )


# 组装写稿用户消息：商品信息 + 每张图的用途与文案
def build_user_prompt(*, name: str, notes: str, product: dict, items: list[dict]) -> str:
    lines = [f"品名：{name}"]
    if product.get("category"):
        lines.append(f"品类：{product['category']}")
    if product.get("selling_points"):
        lines.append("识别到的卖点：" + "；".join(product["selling_points"]))
    if notes.strip():
        lines.append(f"商家补充：{notes.strip()}")
    lines.append("商品图（按顺序）：")
    for it in items:
        copy = "/".join(x for x in [it.get("title"), it.get("subtitle"), *it.get("bullets", [])] if x)
        lines.append(f"{it['idx']}. {it['slot_label']}：{it.get('scene', '')}；图上文案：{copy or '无'}")
    return "\n".join(lines)


# 画面大字：模型给的标题超过 10 字就退回套图上的标题（本身 ≤10 字），避免硬截断成半句
def fit_title(llm_title: str, set_title: str, limit: int = 10) -> str:
    t = (llm_title or "").strip()
    if t and len(t) <= limit:
        return t
    return clip(set_title or t, limit)


# 模型漏写某段时的兜底口播
def fallback_narration(item: dict, *, name: str, first: bool, last: bool) -> str:
    if first:
        return f"今天给你看看这款{name}，用过就知道有多省心。"
    if last:
        return f"喜欢这款{name}的话，点下方链接看看详情。"
    parts = [item.get("title") or "", item.get("subtitle") or ""]
    text = "，".join(p for p in parts if p) or f"这款{name}细节做得很用心"
    return text + "。"


def parse_segments(raw: str, items: list[dict], *, name: str) -> dict[str, Any]:
    """解析模型输出并对齐到套图各张：缺段补兜底、超长截断、极限词替换。

    返回 {"title": str, "segments": [{"idx", "title", "narration"}]}，顺序与 items 一致。
    """
    data: dict[str, Any] = {}
    try:
        data = extract_json(raw)
    except (ValueError, TypeError):
        data = {}
    by_idx: dict[int, dict] = {}
    for seg in data.get("segments") or []:
        if isinstance(seg, dict):
            try:
                by_idx.setdefault(int(seg.get("idx")), seg)
            except (TypeError, ValueError):
                continue
    segments: list[dict] = []
    for pos, it in enumerate(items):
        src = by_idx.get(int(it["idx"])) or {}
        narration = str(src.get("narration") or "").strip()
        if len(narration) < SEG_MIN_CHARS // 2:
            narration = fallback_narration(it, name=name, first=pos == 0, last=pos == len(items) - 1)
        fields, audit = clean_fields({
            "narration": clip(narration, SEG_MAX_CHARS),
            "overlay_title": fit_title(str(src.get("title") or ""), it.get("title") or ""),
            "video_prompt": clip(str(src.get("video") or "").strip(), 60),
        })
        segments.append({"idx": int(it["idx"]), "title": fields["overlay_title"],
                         "narration": fields["narration"], "compliance_changes": audit,
                         "motion": src.get("motion") is True or str(src.get("motion")).lower() == "true",
                         "action": str(src.get("action") or "").strip(),
                         "video": fields["video_prompt"]})
    fields, audit = clean_fields({"title": clip(str(data.get("title") or name), 16)})
    # 回退品名同样生成可追溯清洗结果，不把已删除的极限词重新带回标题。
    if not fields["title"]:
        fields, audit = clean_fields({"title": clip(name, 16)})
    return {"title": fields["title"], "segments": segments, "compliance_changes": audit}


# 无 Key 联调时的固定口播
def mock_segments(items: list[dict], *, name: str) -> dict[str, Any]:
    segs = [
        {"idx": int(it["idx"]), "title": it.get("title") or "",
         "narration": fallback_narration(it, name=name, first=i == 0, last=i == len(items) - 1),
         "motion": i == len(items) // 2, "action": "push_in" if i == 0 else "light_sweep", "video": ""}
        for i, it in enumerate(items)
    ]
    return {"title": clip(name, 16), "segments": segs}
