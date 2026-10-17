"""站点预设模型清单 + 按模型生成参数白名单。"""

from __future__ import annotations

from typing import Any

from app.schemas_routing import LogicalModelCapability
from app.services.model_routing_config import normalize_model_name

# 管理端四下拉 + 渠道 models 硬编码白名单（含前台展示文案）
# id 与 TokenFree 目录展示名对齐（点分：seedance-2.0 / seedream-5.0-pro）
PRESET_MODELS: dict[LogicalModelCapability, tuple[dict[str, str], ...]] = {
    "text": (
        {
            "id": "kimi-k2.6",
            "label": "Kimi K2.6",
            "note": "默认剧本/分镜，中文长上下文表现稳定",
            "pricing_hint": "适合长剧本",
        },
        {
            "id": "deepseek-v4-pro",
            "label": "DeepSeek V4 Pro",
            "note": "推理强、性价比高，适合扩写与润色",
            "pricing_hint": "通常更省",
        },
        {
            "id": "gpt-5.5",
            "label": "GPT 5.5",
            "note": "综合能力强，复杂指令与英文场景更稳",
            "pricing_hint": "相对更贵",
        },
    ),
    "image": (
        {
            "id": "seedream-5.0-pro",
            "label": "Seedream 5.0 Pro",
            "note": "成片定妆首选，角色一致性好；按清晰度计费",
            "pricing_hint": "清晰度越高越贵",
            "eta_hint": "约 20–60 秒/张",
        },
        {
            "id": "seedream-5.0",
            "label": "Seedream 5.0",
            "note": "标准静帧，质量与成本均衡",
            "pricing_hint": "约 ¥0.33/张",
            "eta_hint": "约 15–40 秒/张",
        },
        {
            "id": "seedream-5.0-flash",
            "label": "Seedream 5.0 Flash",
            "note": "更快更省，适合草稿与批量试错",
            "pricing_hint": "更省 · 约 ¥0.18/张",
            "eta_hint": "约 10–25 秒/张",
        },
        {
            "id": "gpt-image-2",
            "label": "GPT Image 2",
            "note": "OpenAI 生图，光影与文字表现好，适合海报与精细静帧",
            "pricing_hint": "约 ¥0.55/次",
            "eta_hint": "约 30–90 秒/张",
        },
        {
            "id": "gpt-image-2.5",
            "label": "GPT Image 2.5",
            "note": "OpenAI 新一代生图，细节与文字更稳，适合定妆与精细静帧",
            "pricing_hint": "按次计费，通常高于 2.0",
            "eta_hint": "约 30–120 秒/张",
        },
    ),
    "video": (
        {
            "id": "seedance-2.5",
            "label": "Seedance 2.5",
            "note": "成片首选，画质更好；4–30 秒，支持到 1080p",
            "pricing_hint": "相对更贵 · 清晰度越高越贵",
            "eta_hint": "约 3–8 分钟/镜",
        },
        {
            "id": "seedance-2.0",
            "label": "Seedance 2.0",
            "note": "标准成片，运镜较稳；4–15 秒，最高 720p",
            "pricing_hint": "清晰度越高越贵",
            "eta_hint": "约 2–5 分钟/镜",
        },
        {
            "id": "seedance-2.0-fast",
            "label": "Seedance 2.0 Fast",
            "note": "2.0 加速档，适合赶进度；4–15 秒，最高 720p",
            "pricing_hint": "介于标准与 Mini 之间",
            "eta_hint": "约 1–4 分钟/镜",
        },
        # seedance-2.0-mini：TokenFree 会转到 doubao-seedance-2-0-mini-260615，
        # 上游 Ark 未开通 → ModelNotOpen；暂不进预设，别名钳到 2.0
        {
            "id": "MiniMax-H3",
            "label": "MiniMax H3",
            "note": "节奏感强、人物生动；4–15 秒，仅 720p",
            "pricing_hint": "出片偏慢 · 约 ¥0.44–0.73/秒",
            "eta_hint": "偏慢，4s 约 15 分钟起",
        },
    ),
    "audio": (
        {
            "id": "qwen-tts-2025-05-22",
            "label": "Qwen TTS",
            "note": "阿里云百炼 Qwen 配音，站点唯一语音预设",
            "pricing_hint": "按 Token 计费",
            "eta_hint": "约数秒～十几秒",
        },
    ),
}

# 生图常用比例（与 Seedream 尺寸表交集）
IMAGE_ASPECT_RATIOS: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
)
# Seedream / GPT Image 2：前端只给 1K/2K，避免选更高档被静默降档
IMAGE_RESOLUTIONS_STANDARD: tuple[str, ...] = ("1K", "2K")
# GPT Image 2.5 支持到 4K
IMAGE_RESOLUTIONS_GPT_IMAGE_25: tuple[str, ...] = ("1K", "2K", "4K")

VIDEO_ASPECT_RATIOS: tuple[str, ...] = ("9:16", "16:9", "1:1")
VIDEO_RESOLUTIONS_FULL: tuple[str, ...] = ("480p", "720p", "1080p")
VIDEO_RESOLUTIONS_SAFE: tuple[str, ...] = ("480p", "720p")
# MiniMax-H3 当前渠道仅 720p
VIDEO_RESOLUTIONS_720_ONLY: tuple[str, ...] = ("720p",)


# 历史友好别名 / 旧连字符 id → TokenFree 点分规范 id
PRESET_MODEL_ALIASES: dict[str, str] = {
    "seedance-2-5": "seedance-2.5",
    "seedance2.5": "seedance-2.5",
    "seedance-2": "seedance-2.0",
    "seedance2": "seedance-2.0",
    "seedance-2-0": "seedance-2.0",
    "seedance2.0": "seedance-2.0",
    # Mini 上游未开通：历史选择一律落到 2.0，避免 ModelNotOpen
    "seedance-2-0-mini": "seedance-2.0",
    "seedance-2.0-mini": "seedance-2.0",
    "doubao-seedance-2-0-mini-260615": "seedance-2.0",
    "seedance-2-0-fast": "seedance-2.0-fast",
    "seedance-2.0-fast": "seedance-2.0-fast",
    "seedream-5-0-pro": "seedream-5.0-pro",
    "seedream-5.0-pro": "seedream-5.0-pro",
    "seedream-5-0": "seedream-5.0",
    "seedream-5": "seedream-5.0",
    "seedream5.0": "seedream-5.0",
    "seedream5": "seedream-5.0",
    "seedream-5-0-flash": "seedream-5.0-flash",
    "seedream-5.0-flash": "seedream-5.0-flash",
    "gpt-image-2-5": "gpt-image-2.5",
    "gpt-image-2.5": "gpt-image-2.5",
    "gpt-image-2.5-sunburst": "gpt-image-2.5",
    "gpt-image-2-5-sunburst": "gpt-image-2.5",
    "gpt-image-2.5-flare": "gpt-image-2.5",
    "gpt-image-2-5-flare": "gpt-image-2.5",
    "gemini-3.1-flash-tts": "qwen-tts-2025-05-22",
    "gemini-3.1-flash": "qwen-tts-2025-05-22",
}


def preset_ids(capability: LogicalModelCapability) -> list[str]:
    """某能力下的预设上游 id。"""
    return [str(row["id"]) for row in PRESET_MODELS.get(capability, ())]


def resolve_preset_alias(model_id: str | None) -> str:
    """将历史别名映射到预设规范 id；无映射则原样返回。"""
    raw = (model_id or "").strip()
    if not raw:
        return ""
    key = normalize_model_name(raw)
    for alias, canonical in PRESET_MODEL_ALIASES.items():
        if normalize_model_name(alias) == key:
            return canonical
    return raw


def lookup_preset_row(model_id: str | None) -> dict[str, str] | None:
    """按 id（含别名）查找预设行。"""
    mid = normalize_model_name(resolve_preset_alias(model_id))
    if not mid:
        return None
    for rows in PRESET_MODELS.values():
        for row in rows:
            if normalize_model_name(row["id"]) == mid:
                return dict(row)
    return None


def preset_display_meta(model_id: str | None) -> dict[str, str]:
    """前台目录展示：友好名、介绍、资费与大致出片时长。"""
    row = lookup_preset_row(model_id)
    if not row:
        return {"label": "", "note": "", "pricing_hint": "", "eta_hint": ""}
    return {
        "label": str(row.get("label") or ""),
        "note": str(row.get("note") or ""),
        "pricing_hint": str(row.get("pricing_hint") or ""),
        "eta_hint": str(row.get("eta_hint") or ""),
    }


def all_preset_channel_models() -> list[str]:
    """写入 TokenFree 渠道的全部预设 id（文/图/视/音）。"""
    out: list[str] = []
    seen: set[str] = set()
    for cap in ("text", "image", "video", "audio"):
        for mid in preset_ids(cap):  # type: ignore[arg-type]
            key = normalize_model_name(mid)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(mid)
    return out


def is_preset_model(capability: LogicalModelCapability, model_id: str | None) -> bool:
    """默认模型是否落在对应预设内（含历史别名）。"""
    mid = normalize_model_name(resolve_preset_alias(model_id))
    if not mid:
        return False
    return mid in {normalize_model_name(x) for x in preset_ids(capability)}


def clamp_default_to_preset(capability: LogicalModelCapability, model_id: str | None) -> str:
    """非法或不空缺省时落到该能力第一项预设。"""
    raw = resolve_preset_alias(model_id)
    if is_preset_model(capability, raw):
        # 返回预设表里的规范写法
        for mid in preset_ids(capability):
            if normalize_model_name(mid) == normalize_model_name(raw):
                return mid
    ids = preset_ids(capability)
    return ids[0] if ids else ""


def _is_minimax_h3(mid: str) -> bool:
    return "minimax" in mid and "h3" in mid.replace("-", "").replace("_", "")


def _is_seedance_mini(mid: str) -> bool:
    return "seedance" in mid and "mini" in mid


def _is_seedance_fast(mid: str) -> bool:
    return "seedance" in mid and "fast" in mid


def _is_seedance_25(mid: str) -> bool:
    return "seedance" in mid and ("2.5" in mid or "2-5" in mid)


def _is_seedance_20(mid: str) -> bool:
    """Seedance 2.0 完整档（非 mini / fast / 2.5）。"""
    if "seedance" not in mid:
        return False
    if _is_seedance_mini(mid) or _is_seedance_fast(mid) or _is_seedance_25(mid):
        return False
    return (
        "2-0" in mid
        or "2.0" in mid
        or mid in {"seedance-2", "seedance2"}
        or mid.endswith("seedance-2")
    )


def model_generation_options(model_id: str | None) -> dict[str, Any]:
    """按模型返回前台可选参数；未知模型给保守默认。"""
    mid = normalize_model_name(resolve_preset_alias(model_id) or (model_id or ""))
    if not mid:
        return {}

    image_ids = {normalize_model_name(x) for x in preset_ids("image")}
    video_ids = {normalize_model_name(x) for x in preset_ids("video")}

    # 图像：Seedream / GPT Image 2 为 1K·2K；GPT Image 2.5 开放 4K
    if mid in image_ids or "seedream" in mid or mid.startswith("gpt-image"):
        resolutions = (
            list(IMAGE_RESOLUTIONS_GPT_IMAGE_25)
            if "gpt-image-2.5" in mid or "gpt-image-2-5" in mid
            else list(IMAGE_RESOLUTIONS_STANDARD)
        )
        return {
            "capability": "image",
            "allowed_aspect_ratios": list(IMAGE_ASPECT_RATIOS),
            "allowed_resolutions": resolutions,
        }

    # 视频：预设表与 Seedance / MiniMax 优先
    # Seedance 2.0 / fast / mini：官方输出时长 4–15s；2.5：4–30s（见方舟文档）
    if mid in video_ids or "seedance" in mid or _is_minimax_h3(mid):
        if _is_seedance_mini(mid) or _is_seedance_fast(mid):
            return {
                "capability": "video",
                "allowed_aspect_ratios": list(VIDEO_ASPECT_RATIOS),
                "allowed_resolutions": list(VIDEO_RESOLUTIONS_SAFE),
                "duration_min": 4,
                "duration_max": 15,
            }
        if _is_minimax_h3(mid):
            return {
                "capability": "video",
                "allowed_aspect_ratios": list(VIDEO_ASPECT_RATIOS),
                "allowed_resolutions": list(VIDEO_RESOLUTIONS_720_ONLY),
                "duration_min": 4,
                "duration_max": 15,
            }
        # 仅 Seedance 2.5 开放 1080p 与 30s；2.0 及其它 Seedance 最高 720p / 15s
        if _is_seedance_25(mid):
            return {
                "capability": "video",
                "allowed_aspect_ratios": list(VIDEO_ASPECT_RATIOS),
                "allowed_resolutions": list(VIDEO_RESOLUTIONS_FULL),
                "duration_min": 4,
                "duration_max": 30,
            }
        if _is_seedance_20(mid) or "seedance" in mid:
            return {
                "capability": "video",
                "allowed_aspect_ratios": list(VIDEO_ASPECT_RATIOS),
                "allowed_resolutions": list(VIDEO_RESOLUTIONS_SAFE),
                "duration_min": 4,
                "duration_max": 15,
            }
        return {
            "capability": "video",
            "allowed_aspect_ratios": list(VIDEO_ASPECT_RATIOS),
            "allowed_resolutions": list(VIDEO_RESOLUTIONS_SAFE),
            "duration_min": 4,
            "duration_max": 15,
        }

    return {}
