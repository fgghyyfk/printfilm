"""一图出套图：看商品图（或仅凭文字）规划整套图的画面与文案。"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.ecom_set.presets import PLATFORMS, Slot, normalize_platform
from app.services.ecom_set.prompts import (
    PLAN_SYSTEM,
    COPY_LIMITS,
    build_plan_user_text,
    clip,
    normalize_copy,
    sanitize_scene,
)
from app.services.compliance.ad_words import Hit, summarize
from app.services.llm_client import chat_completions, chat_completions_vision

logger = logging.getLogger(__name__)

VISION_MAX_EDGE = 1024


# 把商品图缩到长边 ≤1024 并转成 JPEG data URI，节省视觉 token
def image_data_uri(path: Path) -> str:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# 从模型输出里取出第一个 JSON 对象（兼容 ```json 包裹与前后废话）
def extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("规划结果不是 JSON")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("规划结果格式错误")
    return data


# 把模型返回的 items 对齐到槽位列表：缺的补默认、多的丢掉、文案截断并过滤极限词
def align_items(raw_items: Any, slots: list[Slot], *, name: str, language: str = "zh") -> tuple[list[dict], list[Hit]]:
    by_slot: dict[str, dict] = {}
    ordered: list[dict] = []
    for it in raw_items if isinstance(raw_items, list) else []:
        if isinstance(it, dict):
            ordered.append(it)
            key = str(it.get("slot") or "")
            if key and key not in by_slot:
                by_slot[key] = it
    hits: list[Hit] = []
    items: list[dict] = []
    valid = {s.key for s in slots}
    for i, slot in enumerate(slots):
        src = by_slot.get(slot.key)
        if src is None:
            # 只有该位置的项没标已知槽位时才按位置兜底，避免错配别的槽位的文案
            pos = ordered[i] if i < len(ordered) else {}
            src = pos if str(pos.get("slot") or "") not in valid else {}
        copy, h = normalize_copy(src, layout=slot.layout, language=language)
        hits += h
        if language == "zh" and slot.layout != "none" and not copy["title"]:
            copy["title"] = clip(name, COPY_LIMITS[language][0])
        scene, sh = sanitize_scene(str(src.get("scene") or ""))
        hits += sh
        items.append({"slot": slot.key, "scene": clip(scene, 300), "copy": copy})
    return items, hits


# 规整商品信息
def normalize_product(raw: Any, *, name: str) -> dict:
    body = raw if isinstance(raw, dict) else {}
    colors = [clip(str(c), 12) for c in list(body.get("colors") or [])[:4] if str(c).strip()]
    points = [clip(str(p), 30) for p in list(body.get("selling_points") or [])[:5] if str(p).strip()]
    return {
        "name": clip(str(body.get("name") or name), 40),
        "category": clip(str(body.get("category") or ""), 30),
        "colors": colors,
        "selling_points": points,
    }


# 无 Key 联调（ARK_MOCK）时的固定规划
def mock_plan(slots: list[Slot], *, name: str, language: str = "zh") -> dict:
    items = []
    for slot in slots:
        items.append({
            "slot": slot.key,
            "scene": f"{name}，{slot.label}示意画面",
            "title": f"{name[:6]}·{slot.label[:4]}",
            "subtitle": "好物推荐 品质之选",
            "bullets": ["做工扎实", "设计贴心", "日常好用"],
        })
    if language in {"en", "ja", "none"}:
        samples = {"en": ("Everyday comfort", "Made for daily life", ["Thoughtful design", "Easy to use"]),
                   "ja": ("毎日の暮らしに", "使いやすさを身近に", ["丁寧な仕上げ", "日常に寄り添う"]),
                   "none": ("", "", [])}
        for item in items:
            item["title"], item["subtitle"], item["bullets"] = samples[language]
    return {"product": {"name": name, "category": "", "colors": [], "selling_points": []}, "items": items}


async def plan_set(
    *,
    image_path: Path,
    name: str,
    notes: str,
    platform: str,
    style: str,
    slots: list[Slot],
    language: str = "zh",
) -> dict:
    """规划整套图。返回 {"product", "items":[{slot, scene, copy}], "warnings", "vision"}。

    优先用视觉模型看图；视觉不可用或失败时退化为纯文字规划并写入 warnings。
    """
    settings = get_settings()
    platform_name = PLATFORMS[normalize_platform(platform)][0]
    user_text = build_plan_user_text(
        name=name, notes=notes, platform_name=platform_name, style=style, slots=slots, language=language
    )
    warnings: list[str] = []
    vision = False
    if settings.ark_mock:
        data = mock_plan(slots, name=name, language=language)
    else:
        data = None
        if (settings.model_vision or "").strip():
            try:
                text = await chat_completions_vision(
                    PLAN_SYSTEM, user_text, [image_data_uri(image_path)],
                    response_format={"type": "json_object"},
                )
                data = extract_json(text)
                vision = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("视觉规划失败，退化为文字规划: %s", exc)
        if data is None:
            warnings.append("未能识别商品图片，已按品名和补充信息规划；补充卖点效果更好")
            text = await chat_completions(
                PLAN_SYSTEM, user_text + "\n（本次无法提供图片）",
                temperature=0.6, max_tokens=4096, timeout=180.0,
            )
            data = extract_json(text)
    items, hits = align_items(data.get("items"), slots, name=name, language=language)
    warnings += summarize(hits)
    return {
        "product": normalize_product(data.get("product"), name=name),
        "items": items,
        "warnings": warnings,
        "vision": vision,
    }
