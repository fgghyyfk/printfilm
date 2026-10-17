"""TokenFree 公开 /api/pricing：推荐模型官方价与预估。"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services.tokenfree_gateway import TOKENFREE_CONSOLE_URL, tokenfree_site_origin
from app.services.tokenfree_usage import usd_cny_rate

logger = logging.getLogger(__name__)

# New API：model_ratio=1 → $2 / 百万 tokens
NEWAPI_USD_PER_1M_AT_RATIO_1 = 2.0
# 视频价目常见占位倍率，不能当秒价
PLACEHOLDER_MODEL_RATIO = 37.5
_CACHE_TTL_SEC = 3600.0
_FAIL_TTL_SEC = 60.0
TOKENFREE_PRICING_PATH = "/api/pricing"
# LLM 预估按输入/输出拆分
LLM_PROMPT_SHARE = 0.7
# 费率表视频展示用的对照时长
VIDEO_RATE_SAMPLE_SECONDS = 5.0

# ---------------------------------------------------------------------------
# TokenFree Volcengine Ark 渠道价目（2026-10-15 导出）
# pricing_formula: billing_expr USD sell = official_CNY / 7.3 * 1.5
# 本站兑回人民币：official_CNY × 1.5（与 BILLING_USD_CNY=7.3 一致）
# ---------------------------------------------------------------------------
TF_SEEDANCE_SELL_MARKUP = 1.5
# 火山刊例价：元 / 百万 tokens（无视频输入=base/1080p；含视频输入=*_video）
SEEDANCE_OFFICIAL_CNY_PER_M: dict[str, dict[str, float]] = {
    "seedance-2.5": {"base": 70.0, "1080p": 77.0, "video": 42.0, "1080p_video": 46.0},
    "seedance-2.0": {
        "base": 46.0,
        "1080p": 51.0,
        "video": 28.0,
        "1080p_video": 31.0,
        "4k": 26.0,
        "4k_video": 16.0,
    },
    "seedance-2.0-fast": {"base": 37.0, "video": 22.0},
}
# TF 渠道导出的完整 billing_expr（权威；公开 /api/pricing 缺失时注入）
SEEDANCE_TF_BILLING_EXPR: dict[str, str] = {
    "seedance-2.5": (
        'u("resolution") == "1080p" && u("video_input") == "video" ? '
        'tier("1080p_video", u("tokens") * (46 / 7.3 * 1.5) / 1000000) : '
        'u("resolution") == "1080p" ? '
        'tier("1080p", u("tokens") * (77 / 7.3 * 1.5) / 1000000) : '
        'u("video_input") == "video" ? '
        'tier("video", u("tokens") * (42 / 7.3 * 1.5) / 1000000) : '
        'tier("base", u("tokens") * (70 / 7.3 * 1.5) / 1000000)'
    ),
    "seedance-2.0": (
        'u("resolution") == "4k" && u("video_input") == "video" ? '
        'tier("4k_video", u("tokens") * (16 / 7.3 * 1.5) / 1000000) : '
        'u("resolution") == "4k" ? '
        'tier("4k", u("tokens") * (26 / 7.3 * 1.5) / 1000000) : '
        'u("resolution") == "1080p" && u("video_input") == "video" ? '
        'tier("1080p_video", u("tokens") * (31 / 7.3 * 1.5) / 1000000) : '
        'u("resolution") == "1080p" ? '
        'tier("1080p", u("tokens") * (51 / 7.3 * 1.5) / 1000000) : '
        'u("video_input") == "video" ? '
        'tier("video", u("tokens") * (28 / 7.3 * 1.5) / 1000000) : '
        'tier("base", u("tokens") * (46 / 7.3 * 1.5) / 1000000)'
    ),
    "seedance-2.0-fast": (
        'u("video_input") == "video" ? '
        'tier("video", u("tokens") * (22 / 7.3 * 1.5) / 1000000) : '
        'tier("base", u("tokens") * (37 / 7.3 * 1.5) / 1000000)'
    ),
}
# 无上游 token：按输出像素公式 W×H×24/1024（16:9 档近似）
SEEDANCE_EST_TOKENS_PER_SEC_BY_RES: dict[str, int] = {
    "480p": 10_000,  # ≈854×480×24/1024
    "720p": 22_000,  # ≈1280×720×24/1024
    "1080p": 49_000,  # ≈1920×1080×24/1024
}


def _seedance_vendor_yuan_5s(model: str, resolution: str) -> float:
    """由刊例价×1.5×估 tok 推导 5 秒兜底人民币（与 TF expr 对齐）。"""
    coeffs = SEEDANCE_OFFICIAL_CNY_PER_M.get(model) or {}
    if not coeffs:
        return 0.0
    res = (resolution or "720p").strip().lower()
    if res == "1080p" and "1080p" in coeffs:
        official = float(coeffs["1080p"])
    else:
        official = float(coeffs.get("base") or 0)
    tok = int(SEEDANCE_EST_TOKENS_PER_SEC_BY_RES.get(res, 22_000)) * int(VIDEO_RATE_SAMPLE_SECONDS)
    return round(tok / 1_000_000.0 * official * TF_SEEDANCE_SELL_MARKUP, 2)


# 本站视频结算兜底价（元 / 5 秒）：由上方 TF 刊例推导；旧 ¥0.50/秒表已作废
VENDOR_VIDEO_YUAN_5S_BY_RES: dict[str, dict[str, float]] = {
    mid: {
        res: _seedance_vendor_yuan_5s(mid, res)
        for res in ("480p", "720p", "1080p")
        if _seedance_vendor_yuan_5s(mid, res) > 0
        and not (mid == "seedance-2.0-fast" and res == "1080p")
    }
    for mid in ("seedance-2.5", "seedance-2.0", "seedance-2.0-fast")
}
# mini / 连字符别名 → 与规范 id 同价
VENDOR_VIDEO_YUAN_5S_BY_RES.update(
    {
        "seedance-2.0-mini": dict(VENDOR_VIDEO_YUAN_5S_BY_RES["seedance-2.0"]),
        "seedance-2-0": dict(VENDOR_VIDEO_YUAN_5S_BY_RES["seedance-2.0"]),
        "seedance-2-0-mini": dict(VENDOR_VIDEO_YUAN_5S_BY_RES["seedance-2.0"]),
        "seedance-2-5": dict(VENDOR_VIDEO_YUAN_5S_BY_RES["seedance-2.5"]),
        "seedance-2-0-fast": dict(VENDOR_VIDEO_YUAN_5S_BY_RES["seedance-2.0-fast"]),
    }
)
# Seedance 秒价表硬底：仅挡住低于旧错误表 ¥0.50/秒 的脏数据
SEEDANCE_VENDOR_FLOOR_YUAN_PER_SEC = 0.5
# MiniMax-H3：约 $0.08/秒（768P）；本站仅开放 720p；人民币按 billing_usd_cny 折算
MINIMAX_H3_USD_PER_SEC_720P = 0.08


def minimax_h3_yuan_per_sec(settings: Settings | None = None) -> float:
    """MiniMax-H3 720p 结算参考秒价（人民币）。"""
    s = settings or get_settings()
    fx = float(getattr(s, "billing_usd_cny", 0) or 7.3)
    return MINIMAX_H3_USD_PER_SEC_720P * fx


# 兼容旧逻辑：480P 秒价（= 5 秒价 / 5）
VENDOR_VIDEO_YUAN_PER_SEC_480P = {
    mid: round(float(tiers["480p"]) / VIDEO_RATE_SAMPLE_SECONDS, 4)
    for mid, tiers in VENDOR_VIDEO_YUAN_5S_BY_RES.items()
    if "480p" in tiers
}
# 相对 480P 的预估倍率（无精确档位且走 token 回退时用；宁多冻）
VIDEO_RESOLUTION_MULT = {
    "480p": 1.0,
    "720p": 2.0,
    "1080p": 4.0,
}
_VIDEO_RES_ORDER = ("480p", "720p", "1080p")
# Kie sunburst 控制台档（USD / credits）；公开 /api/pricing 往往只有笼统 gpt-image-2-5
KIE_SUNBURST_USD_1K = 0.03
KIE_SUNBURST_USD_2K = 0.05
KIE_SUNBURST_USD_4K = 0.08
KIE_SUNBURST_CREDITS_1K = 6
KIE_SUNBURST_CREDITS_2K = 10
KIE_SUNBURST_CREDITS_4K = 16

# 产品里好用、目录有、方便去 TokenFree 核对的短名单
RECOMMENDED_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "kimi-k2.6",
        "capability": "text",
        "label": "Kimi K2.6",
        "note": "默认剧本/分镜，中文长上下文",
        "recommended": True,
    },
    {
        "id": "deepseek-v3.2",
        "capability": "text",
        "label": "DeepSeek V3.2",
        "note": "便宜备选，扩写与闲聊",
        "recommended": False,
    },
    {
        "id": "qwen3.5-plus",
        "capability": "text",
        "label": "Qwen 3.5 Plus",
        "note": "便宜中文日常对话",
        "recommended": False,
    },
    {
        "id": "seedream-5.0-pro",
        "capability": "image",
        "label": "Seedream 5.0 Pro",
        "note": "定妆与分镜静帧首选",
        "recommended": True,
    },
    {
        "id": "seedream-5.0",
        "capability": "image",
        "label": "Seedream 5.0",
        "note": "标准静帧",
        "recommended": False,
    },
    {
        "id": "seedream-5.0-flash",
        "capability": "image",
        "label": "Seedream 5.0 Flash",
        "note": "更快更省草稿档",
        "recommended": False,
    },
    {
        "id": "gpt-image-2",
        "capability": "image",
        "label": "GPT Image 2",
        "note": "OpenAI 生图，约 ¥0.55/次",
        "recommended": False,
    },
    {
        "id": "gpt-image-2.5",
        "capability": "image",
        "label": "GPT Image 2.5",
        "note": "OpenAI 新一代生图，细节与文字更稳",
        "recommended": False,
    },
    {
        "id": "seedance-2.5",
        "capability": "video",
        "label": "Seedance 2.5",
        "note": "默认成片，最长约 30 秒",
        "recommended": True,
    },
    {
        "id": "seedance-2.0",
        "capability": "video",
        "label": "Seedance 2.0",
        "note": "标准 2.0，4–15 秒",
        "recommended": False,
    },
    {
        "id": "MiniMax-H3",
        "capability": "video",
        "label": "MiniMax H3",
        "note": "4–15 秒，仅 720p",
        "recommended": False,
    },
    {
        "id": "qwen-tts-2025-05-22",
        "capability": "audio",
        "label": "Qwen TTS",
        "note": "默认配音；TokenFree 目录按量计费",
        "recommended": True,
    },
)

_cache: dict[str, "OfficialRate"] | None = None
_cache_at: float = 0.0
_cache_failed: bool = False


@dataclass(frozen=True)
class OfficialRate:
    """一条 TokenFree 官方价。"""

    model: str
    billing: str
    usd_per_call: float
    cny_per_call: float
    cny_in_per_1m: float
    cny_out_per_1m: float
    model_ratio: float
    placeholder: bool
    tags: str
    endpoints: tuple[str, ...] = ()
    # 由 tags / supported_endpoint_types 推断；无强信号为 None（勿当成 text）
    capability: str | None = None
    # New API tiered_expr；有表达式时结算以表达式为准，不再把 model_ratio=37.5 当占位
    billing_mode: str = ""
    billing_expr: str = ""


def capability_from_tokenfree_meta(
    tags: str = "",
    endpoints: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """从 TokenFree 价目 tags / supported_endpoint_types 推断能力；无强信号返回 None。"""
    raw_tags = (tags or "").strip().lower().replace("-", " ")
    parts = [p.strip() for p in raw_tags.replace("|", ",").split(",") if p.strip()]
    blob = " ".join(parts)
    ep = [str(x).strip().lower() for x in (endpoints or []) if str(x).strip()]

    if (
        any(token in blob for token in ("tts", "text to speech", "text to dialogue"))
        or "audio" in parts
        or any("audio" in e or "speech" in e for e in ep)
    ):
        return "audio"
    if (
        any("video" in e for e in ep)
        or "video" in parts
        or any(
            token in blob
            for token in (
                "text to video",
                "image to video",
                "video to video",
                "video editing",
                "lip sync",
            )
        )
    ):
        return "video"
    if (
        any("image" in e for e in ep)
        or "image-generation" in ep
        or "image" in parts
        or any(
            token in blob
            for token in ("text to image", "image to image", "image editing", "kie-image")
        )
    ):
        return "image"
    return None


def lookup_tokenfree_capability(model: str, rates: dict[str, OfficialRate] | None = None) -> str | None:
    """查价目缓存中的强分类；未收录或仅聊天类标签则返回 None。"""
    rate = lookup_rate(model, rates)
    if rate is None:
        return None
    return rate.capability


def tokenfree_pricing_url() -> str:
    """公开价目接口，无需 Key。"""
    return f"{tokenfree_site_origin()}{TOKENFREE_PRICING_PATH}"


def cached_rates() -> dict[str, OfficialRate]:
    """内存缓存；未拉过返回空。"""
    return dict(_cache or {})


def set_cached_rates(rates: dict[str, OfficialRate] | None) -> None:
    """写入缓存；None 表示测试清空，空 dict 表示负缓存。"""
    global _cache, _cache_at, _cache_failed
    if rates is None:
        _cache = None
        _cache_at = 0.0
        _cache_failed = False
        return
    _cache = dict(rates)
    _cache_at = time.monotonic()
    _cache_failed = False


def _mark_fetch_failed() -> None:
    """拉取失败：保留旧表，刷新时间戳，避免预扣每次打外网。"""
    global _cache, _cache_at, _cache_failed
    if _cache is None:
        _cache = {}
    _cache_at = time.monotonic()
    _cache_failed = True


def parse_pricing_item(item: dict[str, Any], *, usd_cny: float) -> OfficialRate | None:
    """把 /api/pricing 一行折成人民币。"""
    name = str(item.get("model_name") or "").strip()
    if not name:
        return None
    try:
        qt = int(item.get("quota_type") or 0)
        mr = float(item.get("model_ratio") or 0)
        cr = float(item.get("completion_ratio") or 0)
        mp = float(item.get("model_price") or 0)
        rate = max(0.01, float(usd_cny))
    except (TypeError, ValueError):
        return None
    tags = str(item.get("tags") or "")
    raw_eps = item.get("supported_endpoint_types") or []
    endpoints = tuple(str(x).strip() for x in raw_eps if str(x).strip()) if isinstance(raw_eps, list) else ()
    cap = capability_from_tokenfree_meta(tags, endpoints)
    mode = str(item.get("billing_mode") or "").strip()
    expr = str(item.get("billing_expr") or "").strip()
    if qt == 1:
        return OfficialRate(
            model=name,
            billing="per_call",
            usd_per_call=mp,
            cny_per_call=round(mp * rate, 4),
            cny_in_per_1m=0.0,
            cny_out_per_1m=0.0,
            model_ratio=0.0,
            placeholder=False,
            tags=tags,
            endpoints=endpoints,
            capability=cap,
            billing_mode=mode,
            billing_expr=expr,
        )
    return OfficialRate(
        model=name,
        billing="token",
        usd_per_call=0.0,
        cny_per_call=0.0,
        cny_in_per_1m=round(mr * NEWAPI_USD_PER_1M_AT_RATIO_1 * rate, 4),
        cny_out_per_1m=round(mr * cr * NEWAPI_USD_PER_1M_AT_RATIO_1 * rate, 4),
        model_ratio=mr,
        placeholder=abs(mr - PLACEHOLDER_MODEL_RATIO) < 0.01,
        tags=tags,
        endpoints=endpoints,
        capability=cap,
        billing_mode=mode,
        billing_expr=expr,
    )


def parse_pricing_payload(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, OfficialRate]:
    """解析 TokenFree 价目 JSON。"""
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    rate = usd_cny_rate(settings)
    out: dict[str, OfficialRate] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        parsed = parse_pricing_item(raw, usd_cny=rate)
        if parsed:
            out[parsed.model] = parsed
    return out


def _gpt_image_id_variants(model: str) -> list[str]:
    """gpt-image-2.5 ↔ gpt-image-2-5 等目录写法互查。"""
    mid = (model or "").strip()
    if not mid:
        return []
    out: list[str] = [mid]
    dotted = re.sub(r"(?i)gpt-image-2-5", "gpt-image-2.5", mid)
    hyphen = re.sub(r"(?i)gpt-image-2\.5", "gpt-image-2-5", mid)
    for alt in (dotted, hyphen):
        if alt and alt not in out:
            out.append(alt)
    return out


def lookup_rate(model: str, rates: dict[str, OfficialRate] | None = None) -> OfficialRate | None:
    """按模型 id 精确或忽略大小写查找（含 gpt-image 点分/连字符）。"""
    mid = (model or "").strip()
    table = rates if rates is not None else cached_rates()
    if not mid or not table:
        return None
    candidates = _gpt_image_id_variants(mid)
    try:
        from app.services.media_model_presets import resolve_preset_alias

        aliased = resolve_preset_alias(mid)
        if aliased:
            for alt in _gpt_image_id_variants(aliased):
                if alt not in candidates:
                    candidates.append(alt)
    except Exception:  # noqa: BLE001
        pass
    for cand in candidates:
        hit = table.get(cand)
        if hit:
            return hit
    keys = {c.lower() for c in candidates}
    for name, row in table.items():
        if name.lower() in keys:
            return row
    return None


def video_catalog_id(model: str) -> str:
    """计费预估用：已知别名映射，未知 Seedance 回退 2.5。"""
    mapped = canonicalize_channel_model_id(model)
    if _is_canonical_seedance(mapped):
        return mapped
    return "seedance-2.5"


def _is_canonical_seedance(model: str) -> bool:
    """是否为 TokenFree 目录里的 Seedance id。"""
    return model in {
        "seedance-2.5",
        "seedance-2.0",
        "seedance-2.0-fast",
        "seedance-2.0-mini",
        # 旧连字符
        "seedance-2-5",
        "seedance-2-0",
        "seedance-2-0-mini",
    }


def canonicalize_channel_model_id(model: str) -> str:
    """已知 Seedance 别名收到 TokenFree 目录 id（点分）；对不上则原样返回。"""
    mid = (model or "").strip()
    if "seedance" not in mid.lower():
        return mid
    low = mid.lower()
    # Mini / TF 笔误 mimi：无独立 expr，规范到 2.0（避免提交 doubao-*-mini 报 ModelNotOpen）
    if "mimi" in low or "mini" in low:
        return "seedance-2.0"
    if "fast" in low:
        return "seedance-2.0-fast"
    if any(token in low for token in ("2-5", "2.5", "260628")):
        return "seedance-2.5"
    if any(token in low for token in ("2-0", "2.0", "260128")):
        return "seedance-2.0"
    compact = low.replace("_", "-")
    if compact in {"seedance-2", "seedance2"} or compact.endswith("seedance-2"):
        return "seedance-2.0"
    return mid


def canonicalize_channel_models(models: list[str] | None) -> list[str]:
    """合并 Seedance 别名，保持原顺序。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in models or []:
        mid = canonicalize_channel_model_id(raw)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _markup_charge(cost_fen: int, settings: Settings) -> int:
    """用户预扣金额 = 官方成本，不再乘倍率。"""
    from app.services.billing.pricing import user_charge_fen

    return user_charge_fen(cost_fen, settings)


def _kie_sunburst_tier(size: str | None = "") -> str:
    """Kie sunburst 清晰度档：1k / 2k / 4k。"""
    raw = (size or "").strip().upper().replace(" ", "")
    if raw.startswith("1K"):
        return "1k"
    if raw.startswith("3K") or raw.startswith("4K"):
        return "4k"
    if raw.startswith("2K"):
        return "2k"
    for sep in ("X", "×"):
        if sep not in raw:
            continue
        left, right = raw.split(sep, 1)
        if left.isdigit() and right.isdigit():
            pixels = int(left) * int(right)
            if pixels <= 1_200_000:
                return "1k"
            if pixels >= 8_000_000:
                return "4k"
            return "2k"
        break
    return "2k"


def kie_sunburst_usd_for_size(size: str | None = "") -> float:
    """Kie sunburst 按清晰度：1K $0.03 / 2K $0.05 / 3K·4K $0.08。"""
    return {
        "1k": KIE_SUNBURST_USD_1K,
        "2k": KIE_SUNBURST_USD_2K,
        "4k": KIE_SUNBURST_USD_4K,
    }[_kie_sunburst_tier(size)]


def kie_sunburst_credits_for_size(size: str | None = "") -> int:
    """Kie sunburst 积分：1K 6 / 2K 10 / 4K 16。"""
    return {
        "1k": KIE_SUNBURST_CREDITS_1K,
        "2k": KIE_SUNBURST_CREDITS_2K,
        "4k": KIE_SUNBURST_CREDITS_4K,
    }[_kie_sunburst_tier(size)]


def resolve_billing_image_size(settings: Settings, *, model: str = "", size: str = "") -> str:
    """计费用清晰度：与 ark / resolve_seedream_size 一致（紧凑→1K；Pro/gpt→≤2K）。"""
    from app.services.drama.seedream_options import (
        _is_compact_tokenfree_image_model,
        is_seedream_pro_model,
    )
    from app.services.tokenfree_image import tokenfree_working_image_model

    raw = (size or "").strip() or str(getattr(settings, "ark_image_size", "") or "2K")
    upstream = (model or getattr(settings, "model_image", "") or "").strip()
    working = tokenfree_working_image_model(upstream)
    # z-image / qwen 等生成侧强制 1K，计费同步
    if _is_compact_tokenfree_image_model(working) or _is_compact_tokenfree_image_model(upstream):
        return "1K"
    from app.services.tokenfree_image import gpt_image_caps_at_2k

    if is_seedream_pro_model(upstream) or gpt_image_caps_at_2k(working) or gpt_image_caps_at_2k(upstream):
        if raw.strip().upper() in {"3K", "4K"}:
            return "2K"
    return raw


def seedream_image_tier_facts(size: str | None = "") -> dict[str, int]:
    """为 TokenFree Seedream 按张表达式补齐 images_up_to_1_5k / images_above_1_5k。"""
    raw = (size or "").strip().upper().replace(" ", "")
    above = True
    if raw.startswith("1K") or raw in {"512", "1024", "768"}:
        above = False
    elif raw.startswith(("2K", "3K", "4K")):
        above = True
    else:
        for sep in ("X", "×"):
            if sep not in raw:
                continue
            left, right = raw.split(sep, 1)
            if left.isdigit() and right.isdigit():
                above = max(int(left), int(right)) > 1536
            break
    if above:
        return {"images_up_to_1_5k": 0, "images_above_1_5k": 1, "input_images": 0}
    return {"images_up_to_1_5k": 1, "images_above_1_5k": 0, "input_images": 0}


def charge_fen_official_image(settings: Settings, *, model: str = "", size: str = "") -> int:
    """生图预估：价目里有按次价或表达式时用 TokenFree 规则；否则 Kie 积分档保底。"""
    from app.services.billing.pricing import kie_credits_to_cost_fen
    from app.services.billingexpr import quote_verified_charge_fen
    from app.services.tokenfree_image import is_seedream_family, tokenfree_working_image_model

    raw_model = model or getattr(settings, "model_image", "") or ""
    mid = tokenfree_working_image_model(raw_model)
    resolved = resolve_billing_image_size(settings, model=raw_model, size=size)
    usage_facts: dict[str, Any] = {
        "results": 1,
        "image_count": 1,
        "resolution": (resolved or "").lower(),
    }
    if is_seedream_family(raw_model) or "seedream" in mid.lower():
        usage_facts.update(seedream_image_tier_facts(resolved))
    verified = quote_verified_charge_fen(
        model=mid,
        prompt=0,
        completion=0,
        total=0,
        usage=usage_facts,
        settings=settings,
    )
    if verified is not None and verified > 0:
        return _markup_charge(verified, settings)
    rate = lookup_rate(mid)
    use_kie_table = "sunburst" in mid.lower() or "gpt-image" in mid.lower()
    if use_kie_table:
        credits = kie_sunburst_credits_for_size(resolved or "2K")
        fen = kie_credits_to_cost_fen(credits, settings) or 1
        return _markup_charge(fen, settings)
    # Seedream / 其它：优先官方按张价
    if rate and rate.billing == "per_call" and rate.cny_per_call > 0:
        # round 避免 0.28*100 浮点成 28.0000000004 被 ceil 多收 1 分
        return _markup_charge(max(1, int(round(rate.cny_per_call * 100))), settings)
    # 无价目时：Seedream 与 gpt-image 共用保守 Kie 2K 档保底（非协议等价，仅防 0 冻）
    credits = kie_sunburst_credits_for_size(
        resolved if (use_kie_table or is_seedream_family(raw_model)) else "2K"
    )
    fen = kie_credits_to_cost_fen(credits, settings) or 1
    return _markup_charge(fen, settings)


def charge_fen_official_llm(tokens: int, settings: Settings, *, model: str = "") -> int:
    """LLM 预估：官方 in/out，按 70% 输入 / 30% 输出拆。"""
    from app.services.billing.pricing import charge_fen_for_tokens

    t = max(0, int(tokens))
    mid = (model or getattr(settings, "model_llm", "") or "kimi-k2.6").strip()
    rate = lookup_rate(mid)
    if t and rate and rate.billing == "token" and (rate.cny_in_per_1m or rate.cny_out_per_1m):
        prompt = int(t * LLM_PROMPT_SHARE)
        completion = t - prompt
        yuan = prompt / 1_000_000 * rate.cny_in_per_1m + completion / 1_000_000 * rate.cny_out_per_1m
        return _markup_charge(max(1, int(math.ceil(yuan * 100))), settings)
    _, charge = charge_fen_for_tokens(t, "llm_chat", settings=settings)
    return charge


def normalize_video_resolution(resolution: str | None, settings: Settings | None = None) -> str:
    """预估用清晰度：只认 480p/720p/1080p。"""
    raw = (resolution or "").strip().lower()
    if raw in VIDEO_RESOLUTION_MULT:
        return raw
    fallback = str(getattr(settings or get_settings(), "ark_video_resolution", "") or "480p").strip().lower()
    return fallback if fallback in VIDEO_RESOLUTION_MULT else "480p"


def estimate_seedance_billing_tokens(
    seconds: float,
    resolution: str = "",
    settings: Settings | None = None,
) -> int:
    """无上游 token 时按片长×清晰度估（像素公式）；对齐 TF，不用扁平 4 万盖掉分档。"""
    s = settings or get_settings()
    secs = max(float(seconds or 0), 2.0)
    res = normalize_video_resolution(resolution, s)
    if res in SEEDANCE_EST_TOKENS_PER_SEC_BY_RES:
        tps = int(SEEDANCE_EST_TOKENS_PER_SEC_BY_RES[res])
    else:
        tps = int(getattr(s, "billing_est_seedance_tokens_per_sec", 0) or 22_000)
    return max(1, int(math.ceil(secs * tps)))


def builtin_seedance_official_rates() -> dict[str, OfficialRate]:
    """TF Ark 渠道导出的 Seedance 价目；公开接口缺失时注入。"""
    out: dict[str, OfficialRate] = {}
    for mid, expr in SEEDANCE_TF_BILLING_EXPR.items():
        coeffs = SEEDANCE_OFFICIAL_CNY_PER_M.get(mid) or {}
        sell_per_m = float(coeffs.get("base") or 0) * TF_SEEDANCE_SELL_MARKUP
        out[mid] = OfficialRate(
            model=mid,
            billing="token",
            usd_per_call=0.0,
            cny_per_call=0.0,
            cny_in_per_1m=sell_per_m,
            cny_out_per_1m=sell_per_m,
            model_ratio=0.0,
            placeholder=False,
            tags="video",
            endpoints=("video",),
            capability="video",
            billing_mode="tiered_expr",
            billing_expr=expr,
        )
    return out


def merge_seedance_tf_rates(rates: dict[str, OfficialRate] | None) -> dict[str, OfficialRate]:
    """用 TF 渠道导出的 Seedance expr 覆盖/补齐本地缓存。"""
    merged = dict(rates or {})
    for mid, rate in builtin_seedance_official_rates().items():
        merged[mid] = rate
    return merged


def _is_minimax_video_model(model: str) -> bool:
    """是否 MiniMax 视频模型（勿走 Seedance video_catalog_id 回退）。"""
    return "minimax" in (model or "").strip().lower()


def _resolve_vendor_video_key(model: str) -> str:
    """解析结算表用的规范 id；非 Seedance/表内 id 原样返回。"""
    mid = (model or "").strip()
    if not mid:
        return ""
    if mid in VENDOR_VIDEO_YUAN_5S_BY_RES:
        return mid
    mapped = canonicalize_channel_model_id(mid)
    if _is_canonical_seedance(mapped):
        return mapped
    return mid


def vendor_video_yuan_per_sec(
    model: str,
    resolution: str,
    settings: Settings | None = None,
) -> float | None:
    """本站视频结算兜底秒价；无对应档位返回 None。Seedance 不低于 ¥1.5/秒。"""
    mid = (model or "").strip()
    if not mid:
        return None
    res = normalize_video_resolution(resolution, settings)
    if _is_minimax_video_model(mid):
        return float(minimax_h3_yuan_per_sec(settings))
    key = _resolve_vendor_video_key(mid)
    tiers = VENDOR_VIDEO_YUAN_5S_BY_RES.get(key)
    if not tiers or res not in tiers:
        return None
    rate = float(tiers[res]) / VIDEO_RATE_SAMPLE_SECONDS
    if "seedance" in key.lower():
        rate = max(rate, float(SEEDANCE_VENDOR_FLOOR_YUAN_PER_SEC))
    return rate


def format_video_pricing_hint(model: str) -> str:
    """前台/费率表：按清晰度列出秒价，实扣再乘时长。"""
    mid = (model or "").strip()
    if not mid:
        return ""
    expr_parts = _expr_resolution_hints(mid)
    if expr_parts:
        return "按清晰度×时长 · " + " · ".join(expr_parts)
    if _is_minimax_video_model(mid):
        return f"约 ¥{minimax_h3_yuan_per_sec():.2f}/秒 · 仅 720p"
    key = _resolve_vendor_video_key(mid)
    tiers = VENDOR_VIDEO_YUAN_5S_BY_RES.get(key)
    if not tiers:
        return ""
    parts: list[str] = []
    for res in _VIDEO_RES_ORDER:
        yuan_5s = tiers.get(res)
        if yuan_5s is None:
            continue
        per_sec = float(yuan_5s) / VIDEO_RATE_SAMPLE_SECONDS
        parts.append(f"{res} ¥{per_sec:.2f}/秒")
    return " · ".join(parts)


def _expr_resolution_hints(model: str) -> list[str]:
    """从计价表达式抽出各清晰度的每秒人民币；表达式未写该档则跳过。"""
    from app.services.billingexpr import expression_usd

    rate = lookup_rate(model)
    if rate is None or not rate.billing_expr:
        mapped = canonicalize_channel_model_id(model)
        rate = lookup_rate(mapped) if mapped else None
    if rate is None or not rate.billing_expr or "seconds" not in rate.billing_expr:
        return []
    fx = usd_cny_rate()
    parts: list[str] = []
    for res in _VIDEO_RES_ORDER:
        if f'"{res}"' not in rate.billing_expr:
            continue
        usd = expression_usd(
            rate.billing_expr,
            tokens={},
            usage={"seconds": 1, "resolution": res, "input_video_seconds": 0, "tier": ""},
        )
        if usd is None or usd <= 0:
            continue
        parts.append(f"{res} ¥{usd * fx:.2f}/秒")
    return parts


def charge_fen_official_video(
    seconds: float,
    settings: Settings,
    *,
    model: str = "",
    resolution: str = "",
    tokens: int | None = None,
) -> int:
    """视频费用：优先 TokenFree 表达式（常按 u(tokens)）；否则清晰度秒价表。"""
    from app.services.billing.pricing import charge_fen_for_tokens

    secs = max(float(seconds or 0), 2.0)
    raw = (model or getattr(settings, "model_video", "") or "seedance-2-5").strip()
    res = normalize_video_resolution(resolution, settings)
    from app.services.billingexpr import quote_verified_charge_fen

    # TokenFree Seedance 现价多为 u("tokens")；无上游 token 时按清晰度估，避免 1080p 按扁平 4 万/秒低估
    try:
        tok = int(tokens or 0)
    except (TypeError, ValueError):
        tok = 0
    if tok <= 0:
        tok = estimate_seedance_billing_tokens(secs, res, settings)
    verified = quote_verified_charge_fen(
        model=raw,
        prompt=0,
        completion=0,
        total=tok,
        usage={
            "seconds": secs,
            "resolution": res,
            "input_video_seconds": 0,
            "tier": "",
            "tokens": tok,
        },
        settings=settings,
    )
    if verified is not None and verified > 0:
        return _markup_charge(verified, settings)
    # MiniMax 必须在 video_catalog_id 之前，否则会误落到 seedance-2-5
    if _is_minimax_video_model(raw):
        rate = vendor_video_yuan_per_sec(raw, res, settings)
    else:
        mid = video_catalog_id(raw)
        rate = vendor_video_yuan_per_sec(mid, res, settings)
    if rate is not None:
        return _markup_charge(max(1, int(math.ceil(secs * rate * 100))), settings)
    tok = estimate_seedance_billing_tokens(secs, res, settings)
    _, charge = charge_fen_for_tokens(tok, "seedance2:video0", settings=settings)
    return charge


def build_official_rate_rows(
    rates: dict[str, OfficialRate],
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """管理端推荐模型费率表（含用户价）。"""
    s = settings or get_settings()
    usd_cny = usd_cny_rate(s)
    items: list[dict[str, Any]] = []
    for spec in RECOMMENDED_MODELS:
        rate = lookup_rate(str(spec["id"]), rates)
        official_yuan = 0.0
        basis = "missing"
        rate_label = "TokenFree 价目未收录，请到控制台核对"
        if spec["capability"] == "video" and rate and rate.billing_expr:
            hint = format_video_pricing_hint(str(spec["id"]))
            fen = charge_fen_official_video(5.0, s, model=str(spec["id"]), resolution="720p")
            if hint and fen:
                official_yuan = round(fen / 100.0, 4)
                basis = "billing_expr"
                rate_label = hint
        elif spec["capability"] == "video":
            hint = format_video_pricing_hint(str(spec["id"]))
            tiers = VENDOR_VIDEO_YUAN_5S_BY_RES.get(str(spec["id"]))
            if hint and tiers and "480p" in tiers:
                official_yuan = round(float(tiers["480p"]), 4)
                basis = "vendor_sec"
                listed = ""
                if rate and rate.placeholder:
                    listed = f"；TokenFree 表 model_ratio={rate.model_ratio} 疑似占位"
                rate_label = f"结算参考 {hint}{listed}"
        elif spec["capability"] == "image" and (
            "sunburst" in str(spec["id"]).lower() or "gpt-image" in str(spec["id"]).lower()
        ) and not (rate and rate.billing == "per_call" and rate.usd_per_call > 0):
            from app.services.billing.pricing import kie_fen_per_credit

            credits = kie_sunburst_credits_for_size("2K")
            fen = max(1, int(round(credits * kie_fen_per_credit(s))))
            official_yuan = round(fen / 100.0, 4)
            basis = "kie_sunburst"
            rate_label = (
                f"Kie sunburst 2K {credits} 积分 ≈ ¥{official_yuan:.2f}"
                f"（1K {KIE_SUNBURST_CREDITS_1K} / 4K {KIE_SUNBURST_CREDITS_4K} 积分）"
            )
        elif rate and rate.billing == "per_call":
            official_yuan = rate.cny_per_call
            basis = "per_call"
            rate_label = f"TokenFree ${rate.usd_per_call:.4f}/次 ≈ ¥{official_yuan:.4f}"
        elif rate and rate.billing == "token":
            official_yuan = rate.cny_out_per_1m
            basis = "token"
            rate_label = f"TokenFree 输入 ¥{rate.cny_in_per_1m:.4f} / 输出 ¥{rate.cny_out_per_1m:.4f} 每百万"
        items.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "provider": "tokenfree",
                "capability": spec["capability"],
                "recommended": bool(spec["recommended"]),
                "note": spec["note"],
                "basis": basis,
                "rate_label": rate_label,
                "official_cost_yuan": official_yuan,
                "user_charge_yuan": official_yuan,
                "placeholder": bool(rate.placeholder) if rate else False,
                "markup": 1.0,
                "usd_cny": usd_cny,
                "verify_url": TOKENFREE_CONSOLE_URL,
            }
        )
    return items


def media_model_pricing_hint(
    model_id: str,
    *,
    capability: str | None = None,
    rates: dict[str, OfficialRate] | None = None,
    settings: Settings | None = None,
) -> str:
    """前台模型卡片资费短句；有结算参考或价目则生成，否则空串交给预设文案。"""
    raw = (model_id or "").strip()
    if not raw:
        return ""
    s = settings or get_settings()
    table = rates if rates is not None else cached_rates()
    from app.services.media_model_presets import resolve_preset_alias

    key = resolve_preset_alias(raw) or raw
    low = key.lower()

    # Seedance / MiniMax：多清晰度结算参考；禁止 MiniMax 回退成 seedance-2-5
    if "seedance" in low or _is_minimax_video_model(key):
        hint = format_video_pricing_hint(key)
        if hint:
            return hint

    if "gpt-image" in low or "sunburst" in low:
        from app.services.billing.pricing import kie_fen_per_credit

        credits = kie_sunburst_credits_for_size("2K")
        fen = max(1, int(round(credits * kie_fen_per_credit(s))))
        return f"约 ¥{fen / 100.0:.2f}/张 · 2K 参考"

    rate = lookup_rate(key, table) or lookup_rate(raw, table)
    if rate and rate.billing == "per_call" and rate.cny_per_call > 0:
        return f"约 ¥{rate.cny_per_call:.2f}/次"
    if rate and rate.billing == "token" and (rate.cny_in_per_1m > 0 or rate.cny_out_per_1m > 0):
        return f"按 Token 计费"
    return ""


async def fetch_tokenfree_pricing() -> dict[str, Any]:
    """GET TokenFree 公开价目，无需 API Key。"""
    url = tokenfree_pricing_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"TokenFree pricing 网络失败: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"TokenFree pricing HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError("TokenFree 价目返回非 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("TokenFree 价目格式异常")
    return payload


async def ensure_official_rates(settings: Settings | None = None) -> dict[str, OfficialRate]:
    """有缓存且未过期则复用；失败写短 TTL，预扣不因外网挂掉。

    Seedance 始终用 TF Ark 渠道导出的 billing_expr 覆盖，避免公开价目漂移。
    """
    ttl = _FAIL_TTL_SEC if _cache_failed else _CACHE_TTL_SEC
    if _cache is not None and _cache_at and (time.monotonic() - _cache_at) < ttl:
        return merge_seedance_tf_rates(_cache)
    try:
        payload = await fetch_tokenfree_pricing()
        parsed = parse_pricing_payload(payload, settings)
        if parsed:
            merged = merge_seedance_tf_rates(parsed)
            set_cached_rates(merged)
            return merged
        _mark_fetch_failed()
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.info("tokenfree pricing skipped: %s", exc)
        _mark_fetch_failed()
    fallback = merge_seedance_tf_rates(cached_rates())
    if fallback:
        set_cached_rates(fallback)
    return fallback


async def billing_official_rate_rows(settings: Settings | None = None) -> list[dict[str, Any]]:
    """拉取（或复用）官方价后生成推荐模型费率表。"""
    s = settings or get_settings()
    rates = await ensure_official_rates(s)
    return build_official_rate_rows(rates, s)
