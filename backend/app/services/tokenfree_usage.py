"""TokenFree / New API 结算所需的消费日志与人民币分换算。"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services.tokenfree_gateway import (
    TOKENFREE_QUOTA_PER_USD,
    resolve_tokenfree_api_key,
    tokenfree_site_origin,
)

logger = logging.getLogger(__name__)

# 与 TokenFree 控制台一致；价目 billing_expr 里常见系数含 /7.3
DEFAULT_USD_CNY = 7.3


def usd_cny_rate(settings: Settings | None = None) -> float:
    """美元兑人民币，用于把 New API quota/USD 折成上游成本（分）。"""
    s = settings or get_settings()
    try:
        rate = float(getattr(s, "billing_usd_cny", None) or DEFAULT_USD_CNY)
    except (TypeError, ValueError):
        rate = DEFAULT_USD_CNY
    return max(0.01, rate)


def quota_to_cost_fen(quota: Any, settings: Settings | None = None) -> int:
    """New API quota → 人民币分；500000 quota = 1 USD。"""
    try:
        amount = int(quota or 0)
    except (TypeError, ValueError):
        return 0
    if amount <= 0:
        return 0
    usd = amount / float(TOKENFREE_QUOTA_PER_USD)
    return _ceil_fen(usd * usd_cny_rate(settings) * 100)


def _ceil_fen(fen: float) -> int:
    """向上取整到分，并消掉 28.0000004 这类二进制误差。"""
    snapped = round(float(fen), 6)
    if snapped <= 0:
        return 0
    return max(1, int(math.ceil(snapped - 1e-9)))


def _list_from_payload(payload: Any) -> list[dict[str, Any]]:
    """从 data/items/logs 中取出对象列表；success=false 视为空。"""
    if isinstance(payload, dict) and payload.get("success") is False:
        return []
    inner = payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            inner = data
    if isinstance(inner, list):
        return [item for item in inner if isinstance(item, dict)]
    if isinstance(inner, dict):
        for key in ("items", "data", "logs", "quota_data"):
            rows = inner.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


async def _tokenfree_get(path: str, *, params: dict[str, Any] | None = None) -> Any:
    """GET TokenFree 站点路径；Bearer 使用模型页同一把 Key。"""
    key = resolve_tokenfree_api_key()
    if not key:
        raise RuntimeError("未配置 TokenFree API Key，请先在「模型」填写")
    origin = tokenfree_site_origin()
    url = path if path.startswith("http") else f"{origin}{path}"
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"TokenFree HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError("TokenFree 返回非 JSON") from exc


async def _try_tokenfree_get(path: str, *, params: dict[str, Any] | None = None) -> Any | None:
    """可选接口：401/未实现时返回 None，不中断主流程。"""
    try:
        return await _tokenfree_get(path, params=params)
    except RuntimeError as exc:
        logger.info("tokenfree optional GET %s skipped: %s", path, exc)
        return None


async def fetch_tokenfree_consume_logs(
    start_ts: int,
    end_ts: int,
    *,
    model_name: str = "",
) -> list[dict[str, Any]]:
    """拉取 New API 消费日志（type=2）。失败返回空列表，不打断结算。"""
    params: dict[str, Any] = {
        "p": 1,
        "page_size": 100,
        "type": 2,
        "start_timestamp": int(start_ts),
        "end_timestamp": int(end_ts),
    }
    if (model_name or "").strip():
        params["model_name"] = model_name.strip()
    payload = await _try_tokenfree_get("/api/log/self", params=params)
    items = _list_from_payload(payload) if payload is not None else []
    if items:
        return items
    total = _payload_total(payload)
    if total <= 0:
        return []
    params["p"] = 0
    payload = await _try_tokenfree_get("/api/log/self", params=params)
    return _list_from_payload(payload) if payload is not None else []


def _payload_total(payload: Any) -> int:
    """日志响应里的 total；没有则 0。"""
    if not isinstance(payload, dict):
        return 0
    data = payload.get("data")
    raw = data.get("total") if isinstance(data, dict) else payload.get("total")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0
