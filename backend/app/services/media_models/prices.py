"""Versioned retail prices, independent of upstream costs and pricing network calls."""
from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models_settings import AppSettings, MediaPriceVersion
from . import MediaValidationError, get_strategy, strategies

expected_price_version: ContextVar[int | None] = ContextVar("expected_media_price_version", default=None)


def validate_prices(prices: dict) -> dict[str, dict[str, str]]:
    if not isinstance(prices, dict):
        raise MediaValidationError("prices", "必须是价格表")
    result: dict[str, dict[str, str]] = {}
    for model, rows in prices.items():
        strategy = get_strategy(model)
        if strategy.model_id != model or not isinstance(rows, dict):
            raise MediaValidationError("prices", "请使用规范模型 ID 和分辨率价格表")
        result[model] = {}
        for resolution, raw in rows.items():
            if resolution not in strategy.resolutions:
                raise MediaValidationError("resolution", f"{model} 不支持 {resolution}")
            try:
                price = Decimal(str(raw))
                if isinstance(raw, bool) or not price.is_finite() or price <= 0 or price > Decimal("10000"):
                    raise ValueError
                if price != price.quantize(Decimal("0.0001")):
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise MediaValidationError("price", "单价须大于 0、不超过 10000 元，最多四位小数") from None
            result[model][resolution] = format(price, ".4f")
    return result


async def active_book(db, *, include_disabled: bool = False) -> dict[str, Any] | None:
    # Dedicated settings row prevents unrelated model settings saves from losing the pointer.
    pointer = await db.get(AppSettings, "media_pricing", populate_existing=True)
    version = (pointer.config_json or {}).get("active_version") if pointer else None
    if not version:
        return None
    enabled = (pointer.config_json or {}).get("enabled", True)
    if not enabled and not include_disabled:
        return None
    row = await db.get(MediaPriceVersion, int(version))
    if row is None:
        raise RuntimeError("生效媒体价格版本不存在")
    return {"version": row.id, "prices": row.prices, "enabled": enabled}


async def publish_book(db, *, prices: dict, expected_version: int | None, user_id: int) -> dict:
    clean = validate_prices(prices)
    await db.execute(insert(AppSettings).values(id="media_pricing", config_json={}).on_conflict_do_nothing(index_elements=[AppSettings.id]))
    pointer = (await db.execute(select(AppSettings).where(AppSettings.id == "media_pricing").with_for_update().execution_options(populate_existing=True))).scalar_one()
    current = (pointer.config_json or {}).get("active_version")
    if current != expected_version:
        raise HTTPException(409, detail="价格版本已变化，请刷新后重试")
    row = MediaPriceVersion(prices=clean, created_by=user_id)
    db.add(row)
    await db.flush()
    pointer.config_json = {**(pointer.config_json or {}), "active_version": row.id, "enabled": True}
    await db.flush()
    return {"version": row.id, "prices": row.prices}


async def set_pricing_enabled(db, *, enabled: bool, expected_version: int):
    pointer = (await db.execute(select(AppSettings).where(AppSettings.id == "media_pricing").with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not pointer or (pointer.config_json or {}).get("active_version") != expected_version:
        raise HTTPException(409, detail="价格版本已变化，请刷新后重试")
    pointer.config_json = {**pointer.config_json, "enabled": enabled}
    await db.flush()


def quote_items(items: list[dict], book: dict) -> dict:
    if not 1 <= len(items) <= 200:
        raise MediaValidationError("items", "须提供 1–200 条生成明细")
    lines = []
    for item in items:
        strategy = resolve_strategy(str(item.get("model_id") or ""))
        lines.append(strategy.quote(item.get("params") or {}, book["prices"]))
    return {"price_version": book["version"], "items": lines,
            "total_fen": sum(x["total_fen"] for x in lines)}


def resolve_strategy(model_id: str):
    from app.services.model_settings import get_routing_snapshot
    logical = next((x for x in get_routing_snapshot().logical_models if x.id.lower() == model_id.lower()), None)
    strategy = get_strategy(logical.strategy_id if logical and logical.strategy_id else model_id)
    limits = getattr(get_routing_snapshot(), "media_reference_limits", {}).get(strategy.model_id)
    return strategy.with_reference_limits(limits)


def initial_draft() -> dict:
    """Freeze currently available legacy estimates as an editable draft, never auto-publish."""
    from app.config import get_settings
    from app.services.tokenfree_pricing import charge_fen_official_image, charge_fen_official_video
    settings = get_settings()
    prices = {}
    for strategy in strategies():
        rows = {}
        for resolution in strategy.resolutions:
            try:
                amount = (charge_fen_official_image(settings, model=strategy.model_id, size=resolution)
                          if strategy.capability == "image" else
                          charge_fen_official_video(10, settings, model=strategy.model_id, resolution=resolution) / 10)
                if amount > 0:
                    rows[resolution] = format(Decimal(str(amount)) / 100, ".4f")
            except (ValueError, KeyError, TypeError):
                continue
        prices[strategy.model_id] = rows
    return prices
