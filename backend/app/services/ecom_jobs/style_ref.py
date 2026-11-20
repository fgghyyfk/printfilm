"""风格复刻的参考图处理：不把参考图里的商品交给生图模型。

参考设计图（常见是竞品海报）原样发给生图模型时，模型容易直接照抄图里的商品。
这里先让视觉模型把参考图读成一段只讲构图、背景、光线、配色、道具和氛围的文字，
生图时只发产品图 + 这段文字；视觉模型不可用时退回强模糊后的参考图，只保留色块与明暗。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.services.compliance.ad_words import check_text

logger = logging.getLogger(__name__)

BRIEF_MAX_CHARS = 320
MOSAIC_CELLS = 16
BLUR_RATIO = 0.03

BRIEF_SYSTEM = (
    "你是电商视觉设计师。用户给你一张参考设计图，你只提取它的设计风格，供另一件完全不同的商品复用。"
    "按顺序描述：构图与机位、主体在画面中的位置与大小、背景与场景、地面/台面与道具、光线方向与软硬、"
    "主色调与配色、质感与氛围、留白与版式。"
    "严禁描述参考图里的主体商品本身：不要写它的品类、名称、形状、颜色、材质、品牌、logo 和画面上的任何文字，"
    "一律用「主体」代称。不要提任何品牌名。"
    '只输出 JSON：{"brief": "一段不超过 200 字的中文风格描述"}'
)


def _brief_cache(directory: Path, reference: Path) -> Path:
    """同一任务同一参考图只读一次：缓存文件按参考图内容哈希命名。"""
    digest = hashlib.sha1(reference.read_bytes()).hexdigest()[:12]
    return directory / f"style_brief_{digest}.txt"


def _clean_brief(raw: str) -> str:
    """解析视觉模型返回，去掉广告极限词并截断；读不出时返回空串。"""
    from app.services.ecom_set.planner import extract_json

    try:
        text = str((extract_json(raw) or {}).get("brief") or "").strip()
    except (ValueError, TypeError, json.JSONDecodeError):
        text = ""
    text, _ = check_text(text)
    return text[:BRIEF_MAX_CHARS]


async def style_brief(directory: Path, reference: Path) -> str:
    """视觉模型把参考图读成风格描述；失败返回空串，由调用方走模糊参考图兜底。"""
    from app.services.image_ref_inline import encode_ref_image
    from app.services.llm_client import chat_completions_vision

    cache = await asyncio.to_thread(_brief_cache, directory, reference)
    if cache.is_file():
        return cache.read_text(encoding="utf-8").strip()
    try:
        uri = await asyncio.to_thread(encode_ref_image, reference, 1024)
        raw = await chat_completions_vision(BRIEF_SYSTEM, "请提取这张参考设计图的风格。", [uri],
                                            temperature=0.2, max_tokens=800,
                                            response_format={"type": "json_object"})
    except Exception as exc:  # noqa: BLE001 — 视觉不可用时走兜底
        logger.warning("风格参考读图失败，改用模糊参考图 path=%s err=%s", reference, exc)
        return ""
    brief = _clean_brief(raw)
    if brief:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(brief, encoding="utf-8")
    return brief


def blurred_reference(reference: Path, dest: Path) -> Path:
    """强模糊参考图：先缩成长边约 16 格的色块再放大模糊，只留配色、明暗与大致构图，认不出原商品轮廓。"""
    with Image.open(reference) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((1024, 1024))
        size = image.size
        scale = MOSAIC_CELLS / max(size)
        cells = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
        image = image.resize(cells, Image.Resampling.BOX).resize(size, Image.Resampling.BICUBIC)
        image = image.filter(ImageFilter.GaussianBlur(max(6, round(max(size) * BLUR_RATIO))))
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, "JPEG", quality=85)
    return dest


def build_brief_prompt(params: dict, brief: str, product_count: int) -> str:
    """只发产品图时的提示词：风格来自文字描述，画面里只允许出现产品图中的商品。"""
    note, _ = check_text(str(params.get("notes") or ""))
    refs = "、".join(f"图{i + 1}" for i in range(product_count)) or "图1"
    return "。".join(part for part in [
        f"{refs}为产品主体，是画面中唯一的商品",
        f"按以下设计风格拍摄电商主图：{brief}",
        "将所有产品图中的商品一起作为画面主体，保持商品外形、比例、颜色、logo和文字不变，不增删部件",
        "画面中不要出现任何其他商品，不添加品牌、logo、文字、水印或广告语", note,
    ] if part)


def build_blur_prompt(params: dict, product_count: int) -> str:
    """模糊参考图兜底时的提示词：最后一张图只借色调与明暗。"""
    note, _ = check_text(str(params.get("notes") or ""))
    refs = "、".join(f"图{i + 1}" for i in range(product_count)) or "图1"
    return "。".join(part for part in [
        f"{refs}为产品主体，是画面中唯一的商品",
        f"图{product_count + 1}是模糊的风格色板，只参考其配色、明暗分布与大致构图，不要画出其中的任何物体",
        "保持商品外形、比例、颜色、logo和文字不变，不增删部件",
        "画面中不要出现任何其他商品，不添加品牌、logo、文字、水印或广告语", note,
    ] if part)
