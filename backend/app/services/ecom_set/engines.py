"""电商套图出图模型：Seedream（默认）/ GPT Image 2（后台「生图」渠道：OpenAI 官方或 Promptoll）。"""

from __future__ import annotations

from app.models_ecom import EcomSet, EcomSetItem
from app.services.ecom_set.presets import SLOTS


# 出图模型：seedream 默认（主渠道）| gpt-image-2（后台「生图」渠道：OpenAI 官方或 Promptoll）
IMAGE_ENGINES = ("seedream", "gpt-image-2")


def normalize_engine(value: str | None) -> str:
    """规整出图模型，未知回落 seedream。"""
    v = (value or "").strip()
    return v if v in IMAGE_ENGINES else "seedream"


def engine_of(ecom: EcomSet) -> str:
    """套图选的出图模型（旧套图为 seedream）。"""
    plan = ecom.plan or {}
    info = plan.get("styled") if isinstance(plan.get("styled"), dict) else plan
    return normalize_engine((info or {}).get("image_engine"))


def item_engine(ecom: EcomSet, item: EcomSetItem) -> str:
    """单张实际用的出图模型：质地特写不带商品参考图，GPT 会画出无品牌的包装，固定用 Seedream。"""
    engine = engine_of(ecom)
    slot = SLOTS.get(item.slot)
    if engine == "gpt-image-2" and slot is not None and slot.ref == "texture":
        return "seedream"
    return engine


def engine_options() -> list[dict]:
    """前台可选的出图模型：GPT Image 2 只在后台开通了生图渠道时出现。"""
    from app.services.openai_image import openai_image_configured, provider_label

    out = [{"id": "seedream", "name": "Seedream 5.0 Pro", "hint": "默认，出图稳定、性价比高"}]
    if openai_image_configured():
        out.append({"id": "gpt-image-2", "name": f"GPT Image 2（{provider_label()}）",
                    "hint": "画面质感与排版感更强，单价更高"})
    return out


