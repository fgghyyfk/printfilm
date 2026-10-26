"""额度告警：用户里程碑弹窗。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent
from app.services.billing.alerts import (
    acknowledge_user_alert,
    list_pending_user_alerts,
    process_billing_alerts_after_charge,
    process_user_milestone_alert,
)
from tests.conftest import make_user


async def _add_settled_usage(
    db: AsyncSession,
    *,
    user_id: int,
    charge_fen: int,
    cost_fen: int = 0,
) -> UsageEvent:
    ev = UsageEvent(
        user_id=user_id,
        domain="studio",
        capability="llm",
        billing_key="llm_chat",
        model="test",
        total_tokens=100,
        charge_fen=charge_fen,
        cost_fen=cost_fen,
        settled=True,
    )
    db.add(ev)
    await db.flush()
    return ev


@pytest.mark.asyncio
async def test_user_milestone_creates_popup_notification(db_session: AsyncSession, monkeypatch) -> None:
    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr(settings, "billing_user_alert_enabled", True)
    monkeypatch.setattr(settings, "billing_user_alert_interval_fen", 1000)

    user = await make_user(db_session)
    await _add_settled_usage(db_session, user_id=user.id, charge_fen=2500)
    await db_session.commit()

    created = await process_user_milestone_alert(db_session, user, settings=settings)
    await db_session.commit()

    assert len(created) == 1
    assert created[0].milestone_fen == 2000
    assert int(user.billing_alert_last_milestone_fen) == 2000

    pending = await list_pending_user_alerts(db_session, user.id)
    assert len(pending) == 1

    ok = await acknowledge_user_alert(db_session, user.id, pending[0].id)
    assert ok is True
    await db_session.commit()
    pending_after = await list_pending_user_alerts(db_session, user.id)
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_pending_alerts_collapse_to_latest(db_session: AsyncSession, monkeypatch) -> None:
    """积压多条未确认时，拉取 pending 只保留最新一条。"""
    from app.models import BillingAlertNotification

    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr(settings, "billing_user_alert_enabled", True)

    user = await make_user(db_session)
    for fen in (1000, 2000, 3000):
        db_session.add(
            BillingAlertNotification(
                user_id=user.id,
                kind="user_milestone",
                title="消费提醒",
                message=f"m{fen}",
                milestone_fen=fen,
            )
        )
    await db_session.commit()

    pending = await list_pending_user_alerts(db_session, user.id)
    assert len(pending) == 1
    assert pending[0].milestone_fen == 3000
    await db_session.commit()

    leftover = await list_pending_user_alerts(db_session, user.id)
    assert len(leftover) == 1
    assert leftover[0].milestone_fen == 3000


@pytest.mark.asyncio
async def test_process_after_charge_triggers_user_alert(db_session: AsyncSession, monkeypatch) -> None:
    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr(settings, "billing_user_alert_enabled", True)
    monkeypatch.setattr(settings, "billing_user_alert_interval_fen", 1000)

    user = await make_user(db_session)
    await _add_settled_usage(db_session, user_id=user.id, charge_fen=900)
    await _add_settled_usage(db_session, user_id=user.id, charge_fen=200)
    await db_session.commit()

    await process_billing_alerts_after_charge(db_session, user, charged_fen=200, settings=settings)
    await db_session.commit()

    pending = await list_pending_user_alerts(db_session, user.id)
    assert len(pending) == 1
    assert pending[0].milestone_fen == 1000


@pytest.mark.asyncio
async def test_process_after_charge_ignores_legacy_admin_cost_alert_settings() -> None:
    """旧配置曾启用费用告警时，结算也不再聚合上游成本或发邮件。"""
    settings = SimpleNamespace(
        billing_user_alert_enabled=False,
        billing_admin_cost_alert_enabled=True,
        billing_admin_cost_alert_threshold_fen=1000,
        billing_admin_cost_alert_period="all_time",
    )
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = AssertionError("settlement must not query platform costs")

    with patch("app.services.email.send_email", new_callable=AsyncMock) as send_mock:
        await process_billing_alerts_after_charge(db, None, charged_fen=200, settings=settings)

    db.execute.assert_not_awaited()
    send_mock.assert_not_awaited()
