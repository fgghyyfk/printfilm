"""管理端：按日汇总本地扣费与用量。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent


def _utc_today() -> date:
    """UTC 当天日期，与财务列表窗口对齐。"""
    return datetime.now(UTC).date()


async def _local_usage_daily(
    db: AsyncSession,
    *,
    since: date,
    until: date,
) -> dict[str, dict[str, int]]:
    """按日聚合全部 usage_events 的扣费、token 与调用次数。"""
    day_expr = cast(UsageEvent.created_at, Date)
    rows = (
        await db.execute(
            select(
                day_expr.label("day"),
                func.coalesce(func.sum(UsageEvent.charge_fen), 0).label("charge_fen"),
                func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("tokens"),
                func.count(UsageEvent.id).label("calls"),
            )
            .where(day_expr >= since, day_expr <= until)
            .group_by(day_expr)
            .order_by(day_expr.asc())
        )
    ).all()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        key = str(row.day)[:10]
        out[key] = {
            "charge_fen": int(row.charge_fen or 0),
            "tokens": int(row.tokens or 0),
            "calls": int(row.calls or 0),
        }
    return out


async def build_finance_daily_list(
    db: AsyncSession,
    *,
    days: int = 30,
) -> dict[str, Any]:
    """返回近 N 日财务用量序列与汇总，无数据的日期补零。"""
    window_days = max(1, min(90, int(days)))
    today = _utc_today()
    start = today - timedelta(days=window_days - 1)

    local_map = await _local_usage_daily(db, since=start, until=today)
    series: list[dict[str, Any]] = []
    totals = {
        "charge_fen": 0,
        "tokens": 0,
        "calls": 0,
    }

    cur = start
    while cur <= today:
        key = cur.isoformat()
        local_hit = local_map.get(key) or {"charge_fen": 0, "tokens": 0, "calls": 0}
        charge_fen = int(local_hit["charge_fen"])
        tokens = int(local_hit["tokens"])
        calls = int(local_hit["calls"])

        series.append(
            {
                "date": key,
                "charge_fen": charge_fen,
                "tokens": tokens,
                "calls": calls,
            }
        )
        totals["charge_fen"] += charge_fen
        totals["tokens"] += tokens
        totals["calls"] += calls
        cur += timedelta(days=1)

    return {
        "days": window_days,
        "totals": totals,
        "series": series,
    }
