from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import UsageEvent
from app.models_settings import MediaPriceVersion
from app.models_tasks import TaskRun
from app.services.billing.context import billing_scope
from app.services.billing.newapi_bill import apply_log_quota
from app.services.billing.settlement import freeze_for_task, settle_task
from app.services.billing.usage import record_line
from app.services.media_models.planning import prepare_task_media
from app.services.media_models.prices import active_book, publish_book, validate_prices
from app.services.media_models.runtime import current_snapshot, operation_for, pin_route, restore_route
from conftest import make_user


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "1.00001", True, "10001"])
def test_bad_price_rejected(value):
    with pytest.raises(ValueError):
        validate_prices({"seedream-5.0-pro": {"2K": value}})


@pytest.mark.asyncio
async def test_price_versions_are_immutable_and_conflicting_publish_rejected(db_session):
    db = db_session
    a = await publish_book(db, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=1)
    b = await publish_book(db, prices={"seedream-5.0-pro": {"2K": "0.5000"}}, expected_version=a["version"], user_id=1)
    assert (await active_book(db))["version"] == b["version"]
    assert (await db.get(MediaPriceVersion, a["version"])).prices["seedream-5.0-pro"]["2K"] == "0.1234"
    with pytest.raises(HTTPException) as error:
        await publish_book(db, prices={}, expected_version=a["version"], user_id=1)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_partial_delivery_uses_snapshot_and_upstream_cannot_replace_retail(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    await publish_book(db, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=user.id)
    task = TaskRun(domain="api", task_type="v1_image", requested_by=user.id,
                   payload={"model": "seedream-5.0-pro", "resolution": "2K"}, status="running")
    await prepare_task_media(db, task)
    db.add(task)
    await db.flush()
    await freeze_for_task(db, task)
    initial = task.billing_snapshot["items"][0]
    async with billing_scope(task.id, snapshot=task.billing_snapshot):
        event = await record_line(db, user_id=user.id, billing_key="seedream", model="seedream-5.0-pro",
                                  raw={"delivery_id": "persisted-result-1", "media_operation_id": initial["operation_id"],
                                       "usage": {"cost_fen": 500}})
        duplicate = await record_line(db, user_id=user.id, billing_key="seedream", model="seedream-5.0-pro",
                                      raw={"delivery_id": "persisted-result-1", "media_operation_id": initial["operation_id"]})
    assert duplicate.id == event.id
    assert event.charge_fen == 13
    apply_log_quota(event, {"quota": 500000, "id": 98})
    assert event.charge_fen == 13
    assert event.cost_fen > 13
    task.status = "failed"  # Another operation may fail after this delivery.
    result = await settle_task(db, task.id)
    assert result["charged"] == 13
    assert (await settle_task(db, task.id))["charged"] == 13


@pytest.mark.asyncio
async def test_failed_without_delivery_refunds_all(db_session):
    db = db_session
    user = await make_user(db)
    await publish_book(db, prices={"seedance-2.5": {"720p": "0.5000"}}, expected_version=None, user_id=user.id)
    task = TaskRun(domain="api", task_type="v1_video", requested_by=user.id,
                   payload={"model": "seedance-2.5", "resolution": "720p", "duration": 5}, status="running")
    await prepare_task_media(db, task)
    db.add(task)
    await db.flush()
    await freeze_for_task(db, task)
    from app.models_tasks import TaskEvent
    db.add(TaskEvent(task_id=task.id, event_type="upstream.request.failed", payload={
        "media_operation_id": task.billing_snapshot["items"][0]["operation_id"],
        "request_id": "failed-attempt-1", "model": "seedance-2.5",
        "response": {"body": {"status": "failed", "usage": {"cost_fen": 37}}},
    }))
    await db_session.flush()
    task.status = "failed"
    result = await settle_task(db, task.id)
    assert result == {"charged": 0, "refunded": 250}
    event = (await db.execute(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).scalar_one()
    assert event.cost_fen == 37
    assert event.charge_fen == 0
    assert event.billing_detail["delivered"] is False


def test_media_route_does_not_fall_back_to_another_model(monkeypatch):
    from app.schemas_routing import DefaultModels, LogicalModel
    from app.services.model_settings import RoutingSnapshot
    from app.services.logical_model_router import resolve_logical_model_candidates
    snapshot = RoutingSnapshot(channels=[], logical_models=[LogicalModel(id="seedance-2.0", capability="video")], default_models=DefaultModels())
    monkeypatch.setattr("app.services.logical_model_router.get_routing_snapshot", lambda: snapshot)
    with patch("app.services.logical_model_router._routes_for_logical_model") as routes:
        assert resolve_logical_model_candidates("video", "seedance-2.5") == []
        routes.assert_not_called()


@pytest.mark.asyncio
async def test_fixed_price_team_task_uses_owner_wallet_and_member_budget(db_session):
    from tests.test_team_billing import seed_team
    from app.services.billing.settlement import ensure_balance_for_task
    from app.models_team_billing import TeamMonthlyUsage
    team, owner, member = await seed_team(db_session)
    await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=owner.id)
    task = TaskRun(domain="drama", task_type="asset_image", requested_by=member.id, team_id=team.id,
                   payload={"model_id": "seedream-5.0-pro"}, status="pending")
    await prepare_task_media(db_session, task)
    task.billing_estimate_fen = await ensure_balance_for_task(db_session, member, task)
    db_session.add(task)
    await db_session.flush()
    await freeze_for_task(db_session, task)
    async with billing_scope(task.id, snapshot=task.billing_snapshot):
        await record_line(db_session, user_id=member.id, billing_key="seedream", model="seedream-5.0-pro", raw={"delivery_id": "team-image"})
    task.status = "succeeded"
    await settle_task(db_session, task.id)
    assert owner.balance_fen == 1987
    assert member.balance_fen == 90000
    usage = (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.team_id == team.id, TeamMonthlyUsage.user_id == member.id))).scalar_one()
    assert (usage.spent_fen, usage.reserved_fen) == (13, 0)


@pytest.mark.asyncio
async def test_fixed_price_disabled_billing_never_charges_wallet(db_session, monkeypatch):
    from app.config import get_settings
    user = await make_user(db_session)
    await publish_book(db_session, prices={"seedream-5.0-pro": {"2K": "0.1234"}}, expected_version=None, user_id=user.id)
    task = TaskRun(domain="api", task_type="v1_image", requested_by=user.id, payload={"model": "seedream-5.0-pro"}, status="running")
    await prepare_task_media(db_session, task)
    db_session.add(task)
    await db_session.flush()
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    await freeze_for_task(db_session, task)
    async with billing_scope(task.id, snapshot=task.billing_snapshot):
        event = await record_line(db_session, user_id=user.id, billing_key="seedream", raw={"delivery_id": "free-image"})
    task.status = "succeeded"
    result = await settle_task(db_session, task.id)
    assert event.charge_fen == result["charged"] == 0
    assert user.balance_fen == 100000
