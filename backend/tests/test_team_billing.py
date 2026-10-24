"""Team wallet money isolation and reservation lifecycle (isolated test database)."""
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app.models import UsageEvent, WalletLedger
from app.models_tasks import TaskRun
from app.services.billing.settlement import ensure_balance_for_task, freeze_for_task, settle_task
from conftest import make_user


async def seed_team(db, *, limit=1000, balance=2000):
    from app.models_teams import Team, TeamMember
    from app.models_team_billing import TeamWallet
    owner = await make_user(db, balance_fen=balance)
    member = await make_user(db, balance_fen=90000)
    team = Team(name="测试团队", owner_user_id=owner.id)
    db.add(team)
    await db.flush()
    db.add_all([
        TeamMember(team_id=team.id, user_id=owner.id, role="owner", active=True),
        TeamMember(team_id=team.id, user_id=member.id, role="member", active=True, monthly_limit_fen=limit),
        TeamWallet(team_id=team.id, balance_fen=0, frozen_fen=0),
    ])
    await db.flush()
    return team, owner, member


async def enqueue(db, member, team, monkeypatch, *, estimate=300):
    monkeypatch.setattr("app.services.billing.estimates.estimate_task_fen", AsyncMock(return_value=estimate))
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=estimate))
    task = TaskRun(team_id=team.id, requested_by=member.id, domain="drama", task_type="asset_image", status="pending", billing_status="none", payload={})
    task.billing_estimate_fen = await ensure_balance_for_task(db, member, task)
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_team_reserve_freeze_settle_isolated_and_idempotent(db_session, monkeypatch):
    from app.models_team_billing import TeamWallet, TeamMonthlyUsage, TeamWalletLedger
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    usage = (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.user_id == member.id))).scalar_one()
    assert usage.reserved_fen == 300
    assert task.billing_status == "reserved"
    await freeze_for_task(db_session, task)
    await freeze_for_task(db_session, task)
    wallet = owner
    assert (wallet.balance_fen, wallet.frozen_fen, member.balance_fen) == (1700, 300, 90000)
    db_session.add(UsageEvent(user_id=member.id, team_id=team.id, task_run_id=task.id, billing_key="seedream", charge_fen=180, cost_fen=120, settled=False))
    await db_session.flush()
    assert await settle_task(db_session, task.id) == {"charged": 180, "refunded": 120}
    assert await settle_task(db_session, task.id) == {"charged": 180, "refunded": 120}
    assert (wallet.balance_fen, wallet.frozen_fen, usage.spent_fen, usage.reserved_fen) == (1820, 0, 180, 0)
    assert len((await db_session.execute(select(WalletLedger).where(WalletLedger.user_id == owner.id))).scalars().all()) == 3


@pytest.mark.asyncio
async def test_team_monthly_limit_counts_queued_reservations(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session, limit=500)
    await enqueue(db_session, member, team, monkeypatch)
    with pytest.raises(ValueError, match="额度"):
        await enqueue(db_session, member, team, monkeypatch)


@pytest.mark.asyncio
async def test_team_insufficient_wallet_does_not_use_personal_balance(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session, balance=100)
    with pytest.raises(ValueError, match="余额不足"):
        await enqueue(db_session, member, team, monkeypatch)
    assert member.balance_fen == 90000


@pytest.mark.asyncio
async def test_cancel_before_start_releases_team_budget(db_session, monkeypatch):
    from app.models_team_billing import TeamWallet, TeamMonthlyUsage
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    task.status = "cancelled"
    await settle_task(db_session, task.id)
    wallet = owner
    usage = (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.user_id == member.id))).scalar_one()
    assert (wallet.balance_fen, wallet.frozen_fen, usage.reserved_fen) == (2000, 0, 0)


@pytest.mark.asyncio
async def test_disabled_team_billing_keeps_zero_charge_and_no_budget(db_session, monkeypatch):
    from app.config import get_settings
    from app.models_team_billing import TeamWallet, TeamMonthlyUsage
    from app.services.billing.usage import record_line
    team, owner, member = await seed_team(db_session, limit=0, balance=0)
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    task = await enqueue(db_session, member, team, monkeypatch)
    await freeze_for_task(db_session, task)
    event = await record_line(db_session, user_id=owner.id, task_run_id=task.id, billing_key="seedream", tokens=100)
    assert (event.user_id, event.team_id, event.charge_fen) == (member.id, team.id, 0)
    assert await settle_task(db_session, task.id) == {"charged": 0, "refunded": 0}
    assert (await db_session.get(TeamWallet, team.id)).balance_fen == 0
    assert not (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.team_id == team.id))).scalars().all()


@pytest.mark.asyncio
async def test_team_overage_settles_actual_after_member_removed(db_session, monkeypatch):
    from app.models_teams import TeamMember
    from app.models_team_billing import TeamWallet, TeamMonthlyUsage
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    await freeze_for_task(db_session, task)
    membership = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == member.id))).scalar_one()
    membership.active = False
    db_session.add(UsageEvent(user_id=member.id, team_id=team.id, task_run_id=task.id, billing_key="seedream", charge_fen=450, cost_fen=120, settled=False))
    await db_session.flush()
    assert await settle_task(db_session, task.id) == {"charged": 450, "refunded": 0}
    assert owner.balance_fen == 1550
    usage = (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.user_id == member.id))).scalar_one()
    assert (usage.spent_fen, usage.reserved_fen) == (450, 0)


def test_period_uses_beijing_month_boundary():
    from datetime import UTC, datetime
    from app.services.billing.team import billing_period
    assert billing_period(datetime(2026, 9, 30, 15, 59, tzinfo=UTC)) == "2026-09"
    assert billing_period(datetime(2026, 9, 30, 16, 0, tzinfo=UTC)) == "2026-10"


@pytest.mark.asyncio
async def test_team_wallet_and_usage_http_authorization(db_session):
    import httpx
    from fastapi import FastAPI
    from app.api.team_billing import router
    from app.database import get_db
    from app.deps import get_current_user
    from app.models_team_billing import TeamMonthlyUsage
    from app.services.billing.team import billing_period
    team, owner, member = await seed_team(db_session)
    db_session.add_all([TeamMonthlyUsage(team_id=team.id, user_id=owner.id, period=billing_period(), spent_fen=900, reserved_fen=100), TeamMonthlyUsage(team_id=team.id, user_id=member.id, period=billing_period(), spent_fen=200, reserved_fen=300)])
    await db_session.flush()
    app = FastAPI()
    app.include_router(router)
    async def current_db():
        yield db_session
    app.dependency_overrides[get_db] = current_db
    app.dependency_overrides[get_current_user] = lambda: member
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get(f"/teams/{team.id}/wallet")).status_code == 403
        response = await client.get(f"/teams/{team.id}/usage/summary")
        assert response.status_code == 200
        assert response.json()["spent_fen"] == 200
        assert "balance_fen" not in response.json()
        assert (await client.get(f"/teams/{team.id}/usage/records?member_id={owner.id}")).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: owner
        response = await client.get(f"/teams/{team.id}/usage/summary")
        assert response.json()["spent_fen"] == 1100
        assert response.json()["balance_fen"] == 2000


@pytest.mark.asyncio
async def test_legacy_team_callback_credits_original_payer_once(db_session, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.api.billing import router
    from app.database import get_db
    from app.models import Order
    from app.models_team_billing import TeamWallet
    team, owner, member = await seed_team(db_session)
    order = Order(out_trade_no=f"team-test-{team.id}", team_id=team.id, user_id=owner.id, sku_id="test", amount_fen=100, credit_fen=150, pay_type="alipay", status="pending")
    db_session.add(order)
    await db_session.flush()
    from app.services.teams.management import transfer_ownership
    await transfer_ownership(db_session, team.id, owner.id, member.id)
    monkeypatch.setattr("app.api.billing.epay.verify", lambda params: True)
    app = FastAPI()
    app.include_router(router)
    async def current_db():
        yield db_session
    app.dependency_overrides[get_db] = current_db
    params = {"out_trade_no": order.out_trade_no, "money": "1.00", "trade_status": "TRADE_SUCCESS"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/billing/epay/notify", params=params)).status_code == 200
        assert (await client.get("/billing/epay/notify", params=params)).status_code == 200
        assert (await db_session.get(TeamWallet, team.id)).balance_fen == 0
        assert owner.balance_fen == 2150


@pytest.mark.asyncio
async def test_team_callback_rejects_malformed_amount(db_session, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.api.billing import router
    from app.database import get_db
    from app.models import Order
    team, owner, member = await seed_team(db_session)
    order = Order(out_trade_no=f"bad-team-{team.id}", team_id=team.id, user_id=owner.id, sku_id="test", amount_fen=100, credit_fen=150, pay_type="alipay", status="pending")
    db_session.add(order)
    await db_session.flush()
    monkeypatch.setattr("app.api.billing.epay.verify", lambda params: True)
    app = FastAPI()
    app.include_router(router)
    async def current_db():
        yield db_session
    app.dependency_overrides[get_db] = current_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for amount in ("NaN", "", "1.001", "-1"):
            response = await client.get("/billing/epay/notify", params={"out_trade_no": order.out_trade_no, "money": amount, "trade_status": "TRADE_SUCCESS"})
            assert response.status_code == 400
    assert order.status == "pending"


@pytest.mark.asyncio
async def test_personal_history_excludes_team_charges_and_orders(db_session):
    from app.api.billing import list_orders, usage_events, usage_summary
    from app.models import Order
    team, owner, member = await seed_team(db_session)
    db_session.add(UsageEvent(user_id=owner.id, team_id=team.id, billing_key="seedream", charge_fen=100, cost_fen=80))
    db_session.add(Order(out_trade_no=f"history-{team.id}", team_id=team.id, user_id=owner.id, sku_id="test", amount_fen=100, credit_fen=150, pay_type="alipay", status="pending"))
    await db_session.flush()
    result = await usage_events(page=1, page_size=20, db=db_session, user=owner)
    assert result["items"] == []
    assert (await usage_summary(db=db_session, user=owner))["charge_fen"] == 0
    assert (await list_orders(db=db_session, user=owner, limit=50))["orders"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cap_scope", ["member", "team"])
async def test_postgres_concurrent_budget_and_settlement_are_serialized(db_session, monkeypatch, cap_scope):
    """Two real independent PG transactions cannot bypass the same monthly budget."""
    import asyncio
    from sqlalchemy import delete
    from sqlalchemy.engine.url import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.config import get_settings
    from app.models import User
    from app.models_teams import Team, TeamMember
    from app.models_team_billing import TeamMonthlyUsage, TeamWallet, TeamWalletLedger
    from conftest import _resolve_test_database_url
    url, _ = _resolve_test_database_url(get_settings().database_url)
    assert "test" in (make_url(url).database or "")
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    team_id = None
    user_ids = []
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=300))
    try:
        async with sessions() as db:
            team, owner, member = await seed_team(db, limit=500 if cap_scope == "member" else None)
            if cap_scope == "team":
                team.monthly_limit_fen = 500
            team_id = team.id
            user_ids = [owner.id, member.id]
            member_id = member.id
            await db.commit()
        async def submit(actor_id):
            async with sessions() as db:
                actor = await db.get(User, actor_id)
                task = TaskRun(team_id=team_id, requested_by=actor_id, domain="drama", task_type="asset_image", status="pending", billing_status="none", payload={})
                try:
                    task.billing_estimate_fen = await ensure_balance_for_task(db, actor, task)
                    db.add(task)
                    await db.flush()
                    task_id = task.id
                    await db.commit()
                    return task_id
                except ValueError:
                    await db.rollback()
                    return None
        submitted = await asyncio.gather(submit(member_id), submit(member_id if cap_scope == "member" else user_ids[0]))
        assert sum(ident is not None for ident in submitted) == 1
        task_id = next(ident for ident in submitted if ident is not None)
        async with sessions() as db:
            task = await db.get(TaskRun, task_id)
            await freeze_for_task(db, task)
            db.add(UsageEvent(user_id=member_id, team_id=team_id, task_run_id=task_id, billing_key="seedream", charge_fen=180, cost_fen=120, settled=False))
            await db.commit()
        async def settle():
            async with sessions() as db:
                result = await settle_task(db, task_id)
                await db.commit()
                return result
        assert await asyncio.gather(settle(), settle()) == [{"charged": 180, "refunded": 120}] * 2
        async with sessions() as db:
            wallet = await db.get(User, user_ids[0])
            assert (wallet.balance_fen, wallet.frozen_fen) == (1820, 0)
            monthly = (await db.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.team_id == team_id))).scalar_one()
            assert (monthly.spent_fen, monthly.reserved_fen) == (180, 0)
    finally:
        if team_id is not None:
            # Only remove rows belonging to this test's newly committed team/users.
            async with sessions() as db:
                for model in (UsageEvent, TaskRun, TeamMonthlyUsage, TeamWalletLedger, TeamWallet, TeamMember):
                    await db.execute(delete(model).where(model.team_id == team_id))
                await db.execute(delete(Team).where(Team.id == team_id))
                await db.execute(delete(WalletLedger).where(WalletLedger.user_id.in_(user_ids)))
                await db.execute(delete(User).where(User.id.in_(user_ids)))
                await db.commit()
        await engine.dispose()
