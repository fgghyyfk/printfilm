"""用量报表只提供扣费和调用量，保留用户与月份隔离。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing import usage_summary
from app.services.drama.usage_stats import (
    aggregate_drama_usage_by_project_ids,
    empty_usage_stats,
    get_drama_project_usage,
)
from tests.conftest import make_user
from tests.test_admin_stats import _add_usage


@pytest.mark.asyncio
async def test_monthly_usage_summary_excludes_costs_and_other_users(db_session: AsyncSession) -> None:
    user = await make_user(db_session, balance_fen=12345, frozen_fen=50)
    other = await make_user(db_session)
    now = datetime.now(timezone.utc)
    await _add_usage(db_session, user_id=user.id, charge_fen=42, cost_fen=999, total_tokens=17, created_at=now)
    await _add_usage(db_session, user_id=other.id, charge_fen=500, cost_fen=700, total_tokens=900, created_at=now)
    await _add_usage(
        db_session,
        user_id=user.id,
        charge_fen=100,
        total_tokens=250,
        created_at=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1),
    )

    out = await usage_summary(db=db_session, user=user)
    assert "cost_fen" not in out
    assert (out["tokens"], out["calls"], out["charge_fen"], out["charge_yuan"]) == (17, 1, 42, 0.42)
    assert (out["balance_fen"], out["frozen_fen"]) == (12345, 50)
    assert out["period"] == now.strftime("%Y-%m")


@pytest.mark.asyncio
async def test_personal_drama_usage_preserves_user_project_and_call_totals(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    other = await make_user(db_session)
    project_id = 880_001_904
    await _add_usage(
        db_session, user_id=user.id, drama_project_id=project_id,
        charge_fen=60, cost_fen=100, total_tokens=75, billing_key="seedream",
    )
    await _add_usage(
        db_session, user_id=user.id, drama_project_id=project_id,
        charge_fen=15, cost_fen=200, total_tokens=25, billing_key="seedance2:video0",
    )
    await _add_usage(
        db_session, user_id=other.id, drama_project_id=project_id,
        charge_fen=999, total_tokens=1000,
    )
    await _add_usage(db_session, user_id=user.id, drama_project_id=project_id + 1, charge_fen=500)

    out = await get_drama_project_usage(db_session, user_id=user.id, project_id=project_id)
    assert out.model_dump() == {
        "charge_fen": 75, "charge_yuan": 0.75, "tokens": 100,
        "calls": 2, "image_gens": 1, "video_gens": 1,
    }
    missing = await get_drama_project_usage(db_session, user_id=user.id, project_id=project_id + 2)
    assert missing == empty_usage_stats()
    assert "cost_fen" not in missing.model_dump() and "cost_yuan" not in missing.model_dump()
    assert await aggregate_drama_usage_by_project_ids(db_session, user_id=user.id, project_ids=[]) == {}
