"""TokenFree 接口常量、默认配置与仅限本平台的凭据解析。"""

from __future__ import annotations

from app.schemas_routing import SystemModelChannel

TOKENFREE_CHANNEL_ID = "tokenfree"
TOKENFREE_CHANNEL_NAME = "TokenFree New API"
# New API OpenAI 兼容根路径（/channels 是控制台，不是接口）
TOKENFREE_BASE_URL = "https://www.tokenfree.com/v1"
TOKENFREE_CONSOLE_URL = "https://www.tokenfree.com/channels"
# New API 内部额度：500000 quota = 1 USD
TOKENFREE_QUOTA_PER_USD = 500_000


def tokenfree_site_origin(base_url: str | None = None) -> str:
    """把 /v1 兼容根路径收成站点 origin，供 /api/* 与 dashboard 计费用。"""
    raw = (base_url or TOKENFREE_BASE_URL).strip().rstrip("/")
    if raw.endswith("/v1"):
        return raw[: -len("/v1")].rstrip("/")
    return raw


def resolve_tokenfree_api_key() -> str:
    """优先渠道 Key，其次运行时 overlay / 环境变量。"""
    try:
        from app.services.model_settings import get_routing_snapshot

        channels = get_routing_snapshot().channels
    except Exception:  # noqa: BLE001
        channels = []
    for channel in channels:
        if channel.enabled and channel.base_url.rstrip("/") == TOKENFREE_BASE_URL:
            return (channel.api_key or "").strip()
    if channels:
        return ""
    from app.config import get_settings

    s = get_settings()
    return (s.openai_api_key or s.ark_api_key or "").strip()


def locked_tokenfree_channel(
    *,
    api_key: str = "",
    has_api_key: bool | None = None,
    models: list[str] | None = None,
    enabled: bool = True,
) -> SystemModelChannel:
    """构造不可改 Base URL / 协议的唯一渠道。"""
    key = (api_key or "").strip()
    return SystemModelChannel(
        id=TOKENFREE_CHANNEL_ID,
        name=TOKENFREE_CHANNEL_NAME,
        base_url=TOKENFREE_BASE_URL,
        api_key=key,
        has_api_key=bool(key) if has_api_key is None else bool(has_api_key),
        api_format="openai",
        protocol="auto",
        models=list(dict.fromkeys(m.strip() for m in (models or []) if m and m.strip())),
        enabled=enabled,
        sort_order=0,
    )


def pick_migratable_api_key(channels: list[SystemModelChannel]) -> str:
    """只迁 TokenFree 渠道或 base_url 含 tokenfree.com 的 Key，避免把 Moonshot/方舟 Key 写进去。"""
    for channel in channels:
        if channel.id == TOKENFREE_CHANNEL_ID and (channel.api_key or "").strip():
            return (channel.api_key or "").strip()
    for channel in channels:
        key = (channel.api_key or "").strip()
        base = (channel.base_url or "").strip().lower()
        if key and "tokenfree.com" in base:
            return key
    return ""


def tokenfree_media_headers(url: str) -> dict[str, str] | None:
    """仅对 TokenFree 官方 HTTPS origin 附加对应渠道 Key，CDN 不带凭据。"""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.tokenfree.com":
        return None
    key = resolve_tokenfree_api_key()
    return {"Authorization": f"Bearer {key}"} if key else None
