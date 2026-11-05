"""电商出图 kind 注册表：数量、比例、参数校验与提示词构建。"""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd, sqrt
from typing import Callable

from app.services.compliance.ad_words import check_text
from app.schemas_ecom_jobs import BuyerShowParams, SkuParams, StyleParams, TranslateParams
from app.services.ecom_jobs import buyer_show, sku, translate
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
    "translate": Kind(20, ("原比例",), ("2k",), translate.build_prompt),
    "sku": Kind(20, sku.RATIOS, ("2k", "4k"), sku.build_prompt),
    "refine": Kind(20, ("1:1", "3:4", "原比例"), ("2k", "4k"), build_prompt),
    "style": Kind(20, ("1:1", "3:4", "9:16", "16:9"), ("2k", "4k"), build_style_prompt),
    "buyer_show": Kind(9, ("1:1", "3:4", "9:16", "16:9"), ("2k", "4k"), buyer_show.build_prompt),
}


# 严格校验 kind、图片数量和参数，备注经广告词替换后保存。
def validate(kind: str, count: int, params: dict) -> dict:
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError("未知工具")
    if kind == "translate":
        if not isinstance(params, dict) or not 1 <= count <= spec.max_images:
            raise ValueError("参数错误")
        return TranslateParams.model_validate(params).model_dump()
    if kind == "sku":
        if not isinstance(params, dict) or not 1 <= count <= spec.max_images:
            raise ValueError("参数错误")
        clean = SkuParams.model_validate(params).model_dump()
        if clean["sku_names"] and len(clean["sku_names"]) != count:
            raise ValueError("参数错误")
        clean["notes"], _ = check_text(clean["notes"].strip())
        return clean
    if kind == "buyer_show":
        if not isinstance(params, dict) or not 1 <= count <= spec.max_images:
            raise ValueError("参数错误")
        clean = BuyerShowParams.model_validate(params).model_dump()
        clean["notes"], _ = check_text(clean["notes"].strip())
        return clean
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


# 在共同底座保留输入顺序和角色，并给裂变图片添加机位变化。
def build_item_prompt(kind: str, params: dict, inputs: list[dict], idx: int) -> str:
    if kind == "translate":
        return translate.build_prompt(params)
    prompt = (buyer_show.build_item_prompt(params, inputs, idx) if kind == "buyer_show"
              else KINDS[kind].prompt_builder(params))
    descriptions = {
        "product": "产品主体，必须保持外形、颜色、logo与文字",
        "style": "设计风格参考，只借构图、光影与配色",
        "model": "模特外形参考，不复制真人身份或面部，不指定名人",
        "scene": "场景参考，借用生活环境与光线",
        "source": "已完成的成图参考，保留商品特征，变化拍摄角度和取景，不复制人物身份",
    }
    if kind == "sku":
        descriptions["scene"] = "被替换的场景原图，构图、背景、光影、道具与机位必须不变"
        descriptions["source"] = "已完成的成图参考，仅参考商品替换效果，原场景保持不变"
    prompt += "。" + "；".join(f"图{i + 1} 为{descriptions[ref['role']]}" for i, ref in enumerate(inputs))
    if kind != "sku" and any(ref["role"] == "source" for ref in inputs):
        prompt += f"。裂变拍摄变化：{buyer_show.SHOT_VARIATIONS[idx % len(buyer_show.SHOT_VARIATIONS)]}"
    return prompt


# 将原图或所选比例转换为清晰度对应尺寸；保持原比例并对齐生图尺寸到 8 像素。
def image_geometry(ratio: str, clarity: str, original: tuple[int, int]) -> tuple[str, str]:
    width, height = original if ratio == "原比例" else tuple(int(n) for n in ratio.split(":"))
    divisor = gcd(width, height)
    actual_ratio = f"{width // divisor}:{height // divisor}"
    edge = 4096 if clarity == "4k" else 2048
    scale = edge / sqrt(width * height)
    size = f"{max(8, round(width * scale / 8) * 8)}x{max(8, round(height * scale / 8) * 8)}"
    return actual_ratio, size
