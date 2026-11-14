"""商品资料库：上传参考详情（品牌自己的或竞品的）→ 视觉模型读图整理成结构化资料 → 规划文案、信息屏复用。

自己的详情：提取事实（卖点、成分、香调、规格、用法、品牌故事、常见问题）+ 讲述顺序；
竞品详情：只提取讲述顺序与卖点角度（outline / angles），不提取任何事实，避免抄袭与张冠李戴。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.services.compliance.ad_words import REPORT, check_text, summarize

logger = logging.getLogger(__name__)

SLICE_WIDTH = 768
SLICE_MAX_H = 1400      # 长图按约 1:1.8 切片，太长的切片视觉模型看不清小字
BATCH_SLICES = 6        # 每次视觉调用最多带几张切片
MAX_SLICES = 36         # 单次整理最多读多少张切片（约 6 次调用）
MAX_REFERENCES = 20

# 字段：(类型, 每项上限字数 / 子字段, 最多条数)
LIST_FIELDS: dict[str, tuple[tuple[str, int], ...] | int] = {
    "selling_points": (("title", 12), ("desc", 40)),
    "ingredients": (("name", 16), ("desc", 40)),
    "variants": (("name", 30), ("top", 30), ("middle", 30), ("base", 30), ("desc", 30)),
    "specs": (("label", 8), ("value", 30)),
    "faq": (("q", 30), ("a", 60)),
    "usage": 40,
    "scenarios": 20,
    "cautions": 40,
    "outline": 20,
    "angles": 20,
}
LIST_MAX = {"selling_points": 8, "ingredients": 8, "variants": 12, "specs": 10, "faq": 6, "usage": 6,
            "scenarios": 6, "cautions": 5, "outline": 12, "angles": 8}
TEXT_FIELDS = {"summary": 60, "en_name": 40, "story": 240}
# 每类列表的主键（合并去重用）
LIST_KEY = {"selling_points": "title", "ingredients": "name", "variants": "name", "specs": "label", "faq": "q"}

EXTRACT_SYSTEM = """你是电商详情页信息整理员。下面是同一个商品详情页的若干切片（从上到下的顺序）。
只根据图片上真实出现的文字和明确可见的信息整理，看不清或没有的字段留空，绝不编造、不推测数字。
严格只输出一个 JSON 对象：
{
  "summary": "一句话商品定位（≤60字）",
  "en_name": "商品英文名或英文副标（图上有才填）",
  "selling_points": [{"title": "卖点短语≤12字", "desc": "卖点说明≤40字"}],
  "ingredients": [{"name": "成分名", "desc": "作用说明"}],
  "variants": [{"name": "款名/香型名（含编号）", "top": "前调", "middle": "中调", "base": "后调", "desc": "一句话描述"}],
  "specs": [{"label": "品名/规格/保质期/产地/适用人群 等", "value": "值"}],
  "usage": ["使用步骤"],
  "scenarios": ["适用场景"],
  "story": "品牌故事（≤240字）",
  "faq": [{"q": "问题", "a": "回答"}],
  "cautions": ["注意事项"],
  "outline": ["这几张切片依次在讲什么（如：主视觉、香调介绍、成分、使用方法、规格）"]
}"""

OUTLINE_SYSTEM = """你是电商详情页策划。下面是一份竞品详情页的切片（从上到下）。
只分析它的结构，不要摘录任何具体的成分、数据、宣传语原文。严格只输出一个 JSON 对象：
{
  "outline": ["每一屏在讲什么，按顺序，≤20字，如：痛点引入、核心卖点、成分说明、使用对比、规格信息"],
  "angles": ["它从哪些角度打动用户，≤20字，如：香味的情绪价值、随身便携、送礼场景"]
}"""


# ---------- 规整 ----------
def _clip(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def _row_key(field: str, row: dict) -> str:
    """去重键：款式按编号（No.03 与「No.03 伯爵红茶 The Earl」算同一款），其它按主字段。"""
    name = str(row.get(LIST_KEY.get(field, ""), "") or "")
    if field == "variants":
        m = re.search(r"No\.?\s*0*(\d+)", name, re.I)
        if m:
            return f"no{int(m.group(1))}"
    return name.strip().lower()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _merge_variant(base: dict, other: dict) -> None:
    """同一款的两条记录合并：空字段互补，名称优先用带中文、较短的那个。"""
    for k, v in other.items():
        if k != "name" and v and not base.get(k):
            base[k] = v
    a, b = base["name"], other["name"]
    if (_has_cjk(b) and not _has_cjk(a)) or (_has_cjk(a) == _has_cjk(b) and _has_cjk(b) and len(b) < len(a)):
        base["name"] = b


def normalize(raw: Any) -> dict:
    """规整一份商品资料（模型输出或用户编辑都走这里）：裁字数、限条数、去空项。"""
    body = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {k: _clip(body.get(k), n) for k, n in TEXT_FIELDS.items()}
    for field, spec in LIST_FIELDS.items():
        items = body.get(field) if isinstance(body.get(field), list) else []
        clean: list = []
        for it in items:
            if isinstance(spec, int):
                text = _clip(it if not isinstance(it, dict) else next(iter(it.values()), ""), spec)
                if text and text not in clean:
                    clean.append(text)
            elif isinstance(it, dict):
                row = {k: _clip(it.get(k), n) for k, n in spec}
                key = spec[0][0]
                if not row.get(key):
                    continue
                same = next((r for r in clean if _row_key(field, r) == _row_key(field, row)), None)
                if same is None:
                    clean.append(row)
                elif field == "variants":
                    _merge_variant(same, row)
            if len(clean) >= LIST_MAX[field]:
                break
        out[field] = clean
    return out


def is_empty(k: dict | None) -> bool:
    """资料是否为空（没有任何可用内容）。"""
    if not k:
        return True
    return not any(k.get(f) for f in (*TEXT_FIELDS, *LIST_FIELDS))


def merge(parts: list[dict]) -> dict:
    """合并多批整理结果：文本取最长，列表拼接后统一去重（同一款式字段互补）。"""
    combined: dict[str, Any] = {f: "" for f in TEXT_FIELDS}
    for f in LIST_FIELDS:
        combined[f] = []
    for p in parts:
        p = normalize(p)
        for f in TEXT_FIELDS:
            if len(p[f]) > len(combined[f]):
                combined[f] = p[f]
        for f in LIST_FIELDS:
            combined[f] += p[f]
    return normalize(combined)


def compliance_warnings(k: dict) -> list[str]:
    """资料里的极限词 / 违规用语提示（只提示不改字；出图文案会自动替换）。"""
    text = json.dumps(normalize(k), ensure_ascii=False)
    _, hits = check_text(text, mode=REPORT)
    return [f"参考资料：{w}（出图文案会自动替换）" for w in summarize(hits, mode=REPORT)]


def digest(k: dict | None, limit: int = 1600) -> str:
    """压成给规划模型看的资料摘要。"""
    if is_empty(k):
        return ""
    k = normalize(k)
    lines: list[str] = []
    if k["summary"]:
        lines.append(f"定位：{k['summary']}")
    if k["selling_points"]:
        lines.append("卖点：" + "；".join(f"{r['title']}（{r['desc']}）" if r["desc"] else r["title"]
                                         for r in k["selling_points"]))
    if k["ingredients"]:
        lines.append("成分：" + "；".join(f"{r['name']}：{r['desc']}" if r["desc"] else r["name"]
                                         for r in k["ingredients"]))
    if k["variants"]:
        lines.append("款式/香型：" + "；".join(
            r["name"] + "（" + "，".join(x for x in (r["top"], r["middle"], r["base"], r["desc"]) if x) + "）"
            for r in k["variants"]))
    if k["specs"]:
        lines.append("规格：" + "；".join(f"{r['label']}：{r['value']}" for r in k["specs"]))
    for f, label in (("usage", "用法"), ("scenarios", "场景"), ("cautions", "注意")):
        if k[f]:
            lines.append(f"{label}：" + "；".join(k[f]))
    if k["story"]:
        lines.append(f"品牌故事：{k['story']}")
    if k["outline"]:
        lines.append("参考详情的讲述顺序：" + " → ".join(k["outline"]))
    if k["angles"]:
        lines.append("可借鉴的打动角度：" + "；".join(k["angles"]))
    return "\n".join(lines)[:limit]


# ---------- 读图 ----------
def slice_image(path: Path) -> list[str]:
    """把一张（可能很长的）详情图缩到宽 768 后按高度切片，返回 JPEG data URI 列表。"""
    from app.services.ecom_set.composite import flatten_white

    im = flatten_white(path)
    im = ImageOps.exif_transpose(im)
    if im.width > SLICE_WIDTH:
        im = im.resize((SLICE_WIDTH, max(1, round(im.height * SLICE_WIDTH / im.width))), Image.Resampling.LANCZOS)
    out = []
    y = 0
    while y < im.height:
        part = im.crop((0, y, im.width, min(im.height, y + SLICE_MAX_H)))
        if part.height >= 40:
            buf = io.BytesIO()
            part.save(buf, format="JPEG", quality=85)
            out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        y += SLICE_MAX_H
    return out


def plan_batches(own: list[Path], competitor: list[Path]) -> list[tuple[str, list[str]]]:
    """切片并分批：返回 [(kind, [data_uri...])]，总切片数不超过 MAX_SLICES。"""
    batches: list[tuple[str, list[str]]] = []
    total = 0
    for kind, paths in (("own", own), ("competitor", competitor)):
        cur: list[str] = []
        for p in paths:
            for uri in slice_image(p):
                if total >= MAX_SLICES:
                    break
                cur.append(uri)
                total += 1
                if len(cur) >= BATCH_SLICES:
                    batches.append((kind, cur))
                    cur = []
        if cur:
            batches.append((kind, cur))
    return batches


def mock_knowledge(name: str) -> dict:
    """无 Key 联调（ARK_MOCK）时的固定资料。"""
    return normalize({
        "summary": f"{name}，日常随身的香氛护理",
        "selling_points": [{"title": "清爽不黏腻", "desc": "质地轻薄，涂抹后很快吸收"},
                           {"title": "淡雅留香", "desc": "香气柔和，适合日常与通勤"}],
        "specs": [{"label": "品名", "value": name}, {"label": "规格", "value": "60g"}],
        "usage": ["取适量于手背", "双手轻揉至吸收"],
        "outline": ["主视觉", "卖点", "使用方法", "规格"],
    })


async def extract(*, own: list[Path], competitor: list[Path], name: str, mock: bool) -> tuple[dict, list[str], int]:
    """读参考详情整理资料，返回 (资料, 提示, 实际调用次数)。竞品只取结构。"""
    from app.services.ecom_set.planner import extract_json
    from app.services.llm_client import chat_completions_vision

    if mock:
        return mock_knowledge(name), [], 1
    import asyncio

    batches = await asyncio.to_thread(plan_batches, own, competitor)
    if not batches:
        raise ValueError("请先上传参考详情图")
    warnings: list[str] = []
    if sum(len(b) for _, b in batches) >= MAX_SLICES:
        warnings.append(f"参考详情较长，本次只读取了前 {MAX_SLICES} 屏")
    parts: list[dict] = []
    calls = 0
    for i, (kind, uris) in enumerate(batches):
        system = EXTRACT_SYSTEM if kind == "own" else OUTLINE_SYSTEM
        text = f"商品：{name}。这是第 {i + 1} 批切片，共 {len(uris)} 张。"
        try:
            raw = await chat_completions_vision(system, text, uris, temperature=0.2, max_tokens=3000,
                                                response_format={"type": "json_object"})
            calls += 1
            data = extract_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("参考详情整理失败 batch=%s: %s", i, exc)
            warnings.append(f"第 {i + 1} 批切片没读出来，已跳过")
            continue
        if kind == "competitor":
            data = {"outline": data.get("outline"), "angles": data.get("angles")}
        parts.append(data)
    if not parts:
        raise ValueError("没能从参考详情里读出内容，请换清晰一点的图再试")
    k = merge(parts)
    return k, warnings + compliance_warnings(k), calls


def stamp(k: dict, warnings: list[str]) -> dict:
    """存库前加上整理时间与提示。"""
    return {**normalize(k), "warnings": warnings[:10], "updated_at": datetime.now(UTC).isoformat()}
