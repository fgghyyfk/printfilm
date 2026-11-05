"""电商出图 kind 注册表：数量、比例、参数校验与提示词构建。"""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd, sqrt
from typing import Callable

from app.services.compliance.ad_words import check_text
from app.schemas_ecom_jobs import StyleParams
from app.services.ecom_jobs.refine import build_prompt
from app.services.ecom_jobs.style import build_prompt as build_style_prompt


@dataclass(frozen=True)
class Kind:
    """一类电商任务的可复用能力约束。"""

    max_images: int
    ratios: tuple[str, ...]
    clarities: tuple[str, ...]
    prompt_builder: Callable[[dict], str]


STYLE_MODE_LIMITS = {"single": 6, "batch": 20}
KINDS = {
    "refine": Kind(20, ("1:1", "3:4", "原比例"), ("2k", "4k"), build_prompt),
    "style": Kind(20, ("1:1", "3:4", "9:16", "16:9"), ("2k", "4k"), build_style_prompt),
}


# 严格校验 kind、图片数量和参数，备注经广告词替换后保存。
def validate(kind: str, count: int, params: dict) -> dict:
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError("未知工具")
    if kind == "style":
        if not isinstance(params, dict):
            raise ValueError("参数错误")
        clean = StyleParams.model_validate(params).model_dump()
        if not 1 <= count <= STYLE_MODE_LIMITS[clean["mode"]]:
            raise ValueError("产品图数量超限")
        if clean["mode"] == "batch" and clean["count"] != 1:
            raise ValueError("批量模式每个产品仅生成一张")
        clean["notes"], _ = check_text(clean["notes"].strip())
        return clean
    if not 1 <= count <= spec.max_images:
        raise ValueError("请上传 1 至 20 张商品图")
    if not isinstance(params, dict) or set(params) - {"background", "ratio", "clarity", "notes"}:
        raise ValueError("参数错误")
    background, ratio, clarity = params.get("background", "white"), params.get("ratio", "1:1"), params.get("clarity", "2k")
    notes = params.get("notes", "")
    if not all(isinstance(value, str) for value in (background, ratio, clarity)) or background not in {"white", "keep", "transparent"} or ratio not in spec.ratios or clarity not in spec.clarities:
        raise ValueError("参数错误")
    if not isinstance(notes, str) or len(notes) > 500:
        raise ValueError("备注不能超过 500 字")
    clean, _ = check_text(notes.strip())
    return {"background": background, "ratio": ratio, "clarity": clarity, "notes": clean}


# 将原图或所选比例转换为清晰度对应尺寸；保持原比例并对齐生图尺寸到 8 像素。
def image_geometry(ratio: str, clarity: str, original: tuple[int, int]) -> tuple[str, str]:
    width, height = original if ratio == "原比例" else tuple(int(n) for n in ratio.split(":"))
    divisor = gcd(width, height)
    actual_ratio = f"{width // divisor}:{height // divisor}"
    edge = 4096 if clarity == "4k" else 2048
    scale = edge / sqrt(width * height)
    size = f"{max(8, round(width * scale / 8) * 8)}x{max(8, round(height * scale / 8) * 8)}"
    return actual_ratio, size
