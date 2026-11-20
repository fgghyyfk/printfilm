"""视频改走 Promptoll 的 Seedance：只接管视频，主渠道（方舟/TokenFree）继续负责文本与生图。

后台只允许一个生效渠道，这里不新增渠道配置，而是在组装运行时路由时追加一条
只含视频模型的 Promptoll 渠道和对应的 Seedance 逻辑模型。这样报价、固定路由、
任务恢复、轮询与前台模型目录全部沿用标准路由，不需要旁路分支。

Promptoll 的 /v1/contents/generations/tasks 是方舟协议透传（Zenlayer 上游），
请求体与火山方舟一致，所以视频路径不改写成 /videos（见 tokenfree_video.uses_tokenfree_video）。
Key 复用后台「生图」里的 Promptoll Key（zai_ 开头）。
"""

from __future__ import annotations

from typing import Any

from app.schemas_routing import DefaultModels, LogicalModel, LogicalModelBinding, SystemModelChannel

PROMPTOLL_BASE_URL = "https://api.promptoll.com/v1"
PROMPTOLL_VIDEO_CHANNEL_ID = "promptoll-video"
PROMPTOLL_VIDEO_CHANNEL_NAME = "Promptoll 视频"

# 站内策略 id → Promptoll 上的 Seedance 模型 id；排在前面的是默认推荐
SEEDANCE_UPSTREAM: dict[str, str] = {
    "seedance-2.0": "dreamina-seedance-2-0-260128",
    "seedance-2.0-fast": "dreamina-seedance-2-0-fast-260128",
    "seedance-2.5": "dreamina-seedance-2-5-260628",
}
SEEDANCE_LABEL: dict[str, str] = {
    "seedance-2.0": "Seedance 2.0",
    "seedance-2.0-fast": "Seedance 2.0 Fast",
    "seedance-2.5": "Seedance 2.5",
}


def is_promptoll_base(base_url: str | None) -> bool:
    """是否 Promptoll 网关（方舟任务协议，不走 New API /videos 改写）。"""
    return (base_url or "").strip().rstrip("/") == PROMPTOLL_BASE_URL


def is_promptoll_result_url(url: str | None) -> bool:
    """Promptoll 代理的成片/尾帧地址（/contents/generations/tasks/{id}/content|last_frame），下载需带 Key。"""
    raw = (url or "").strip()
    prefix = PROMPTOLL_BASE_URL + "/contents/generations/tasks/"
    return raw.startswith(prefix) and raw.rstrip("/").rsplit("/", 1)[-1] in {"content", "last_frame"}


def promptoll_media_headers(url: str | None, route_key: str = "") -> dict[str, str] | None:
    """只对 Promptoll 代理地址附加 Key（优先任务固定路由的 Key），其它地址一律不带凭据。"""
    if not is_promptoll_result_url(url):
        return None
    key = (route_key or "").strip() or promptoll_video_settings()[1]
    return {"Authorization": f"Bearer {key}"} if key else None


def promptoll_video_settings(flat: dict[str, Any] | None = None) -> tuple[bool, str, str]:
    """读取 (是否开启, Key, 默认模型)；flat 为组装中的运行时配置，缺项回落 Settings。"""
    from app.config import get_settings

    s = get_settings()
    src = flat or {}

    def pick(name: str, fallback: Any) -> Any:
        value = src.get(name)
        return fallback if value is None else value

    enabled = bool(pick("promptoll_video_enabled", s.promptoll_video_enabled))
    key = str(pick("promptoll_api_key", s.promptoll_api_key) or "").strip()
    default = str(pick("promptoll_video_default", s.promptoll_video_default) or "").strip()
    if default not in SEEDANCE_UPSTREAM:
        default = next(iter(SEEDANCE_UPSTREAM))
    return enabled, key, default


def promptoll_video_channel(api_key: str) -> SystemModelChannel:
    """只承载视频模型的运行时渠道（不落库，每次组装路由时生成）。"""
    return SystemModelChannel(
        id=PROMPTOLL_VIDEO_CHANNEL_ID,
        name=PROMPTOLL_VIDEO_CHANNEL_NAME,
        base_url=PROMPTOLL_BASE_URL,
        api_key=api_key,
        has_api_key=bool(api_key),
        api_format="openai",
        protocol="auto",
        models=list(SEEDANCE_UPSTREAM.values()),
        enabled=True,
        sort_order=1000,
    )


def promptoll_video_models() -> list[LogicalModel]:
    """Seedance 逻辑模型，各绑定到 Promptoll 视频渠道上的对应上游模型。"""
    return [
        LogicalModel(
            id=model_id, name=SEEDANCE_LABEL[model_id], capability="video", strategy_id=model_id,
            bindings=[LogicalModelBinding(
                id=f"{PROMPTOLL_VIDEO_CHANNEL_ID}:{model_id}",
                channel_id=PROMPTOLL_VIDEO_CHANNEL_ID, upstream_model=upstream,
            )],
        )
        for model_id, upstream in SEEDANCE_UPSTREAM.items()
    ]


def augment_routing(
    channels: list[SystemModelChannel],
    logical_models: list[LogicalModel],
    defaults: DefaultModels,
    flat: dict[str, Any],
    disabled: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[SystemModelChannel], list[LogicalModel], DefaultModels, dict[str, Any]]:
    """开启时追加 Promptoll 视频渠道与模型；主渠道已有同名视频模型时保留主渠道的。

    渠道排在最后，避免被当作「当前生效配置」取 Key；主渠道没有视频默认模型（或已停用）时
    才把默认视频模型设为 Promptoll 的推荐款，并同步 model_video。"""
    enabled, key, default_id = promptoll_video_settings(flat)
    if not enabled or not key:
        return channels, logical_models, defaults, flat
    existing = {model.id.lower() for model in logical_models}
    added = [
        model.model_copy(update={"enabled": model.id not in disabled})
        for model in promptoll_video_models() if model.id.lower() not in existing
    ]
    if not added:
        return channels, logical_models, defaults, flat
    models = [*logical_models, *added]
    current = defaults.video_model
    usable = {model.id for model in models if model.capability == "video" and model.enabled}
    if current not in usable:
        ordered = [default_id, *SEEDANCE_UPSTREAM]
        current = next((mid for mid in ordered if mid in usable), "")
        defaults = defaults.model_copy(update={"video_model": current})
    out = dict(flat)
    if current and not str(out.get("model_video") or "").strip():
        out["model_video"] = current
    if current and not str(out.get("model_video_2") or "").strip():
        out["model_video_2"] = current
    return [*channels, promptoll_video_channel(key)], models, defaults, out
