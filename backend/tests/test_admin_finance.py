"""管理端财务列表：只统计本地扣费与用量。"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.services.admin.finance import build_finance_daily_list
from tests.conftest import make_user
from tests.test_admin_stats import _add_usage, _utc_days_ago


@pytest.mark.asyncio
async def test_finance_daily_list_includes_local_usage_only(db_session: AsyncSession) -> None:
    """原始计价记录不会增加财务字段，本地扣费、token 与调用数保留。"""
    from app.api.admin.finance import admin_finance_daily

    user = await make_user(db_session)
    target_day = _utc_days_ago(5)
    day_key = target_day.date().isoformat()
    before = await build_finance_daily_list(db_session, days=30)
    before_row = next(item for item in before["series"] if item["date"] == day_key)

    await _add_usage(
        db_session,
        user_id=user.id,
        charge_fen=200,
        cost_fen=120,
        total_tokens=5000,
        created_at=target_day,
        capability="video",
        billing_key="seedance2:video0",
    )
    await db_session.flush()

    out = await admin_finance_daily(days=30, _admin=user, db=db_session)
    after = out.model_dump()
    after_row = next(item for item in after["series"] if item["date"] == day_key)
    assert set(after) == {"days", "totals", "series"}
    assert set(after_row) == {"date", "charge_fen", "tokens", "calls"}
    assert set(after["totals"]) == {"charge_fen", "tokens", "calls"}
    for metric, delta in {"charge_fen": 200, "tokens": 5000, "calls": 1}.items():
        assert after_row[metric] - before_row[metric] == delta
        assert after["totals"][metric] - before["totals"][metric] == delta
        assert after["totals"][metric] == sum(row[metric] for row in after["series"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("requested_days", "expected_days"), [(0, 1), (3, 3), (91, 90)])
async def test_finance_daily_list_pads_and_clamps_window(
    monkeypatch: pytest.MonkeyPatch, requested_days: int, expected_days: int
) -> None:
    from app.services.admin import finance

    monkeypatch.setattr(finance, "_utc_today", lambda: date(2026, 10, 26))
    monkeypatch.setattr(finance, "_local_usage_daily", AsyncMock(return_value={}))
    out = await build_finance_daily_list(AsyncMock(), days=requested_days)

    assert out["days"] == expected_days
    assert len(out["series"]) == expected_days
    assert out["series"][-1]["date"] == "2026-10-26"
    assert [row["date"] for row in out["series"]] == sorted(row["date"] for row in out["series"])
    assert out["totals"] == {"charge_fen": 0, "tokens": 0, "calls": 0}
    assert all(row["charge_fen"] == row["tokens"] == row["calls"] == 0 for row in out["series"])


def test_finance_upstream_routes_are_removed() -> None:
    from app.api.admin import router

    paths = {route.path for route in router.routes}
    assert "/admin/finance/daily" in paths
    assert "/admin/finance/daily/sync" not in paths
    assert "/admin/stats/upstream-usage" not in paths
    assert "/admin/stats/upstream-usage/sync" not in paths
    assert "/admin/settings/billing/model-rates" not in paths
    assert "/admin/settings/tokenfree/quota" not in paths


def test_database_bootstrap_does_not_create_upstream_report_snapshots() -> None:
    assert "usage_events" in Base.metadata.tables
    assert "upstream_usage_daily" not in Base.metadata.tables
