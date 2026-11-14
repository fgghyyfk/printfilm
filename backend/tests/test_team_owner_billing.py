"""所有者统一付款、两级月上限与历史任务兼容。"""
import pytest
from sqlalchemy import select
from app.models import UsageEvent, WalletLedger
from app.models_tasks import TaskRun
from app.models_teams import Team, TeamMember
from app.models_team_billing import TeamMonthlyUsage, TeamWallet
from app.services.billing.settlement import ensure_balance_for_task, freeze_for_task, settle_task
from app.services.billing.team import billing_period
from tests.test_team_billing import seed_team, enqueue


@pytest.mark.asyncio
async def test_owner_pays_actor_and_usage_remain_member(db_session, monkeypatch):
    """只扣所有者个人账户，记录发起成员并允许所有者核对个人账单。"""
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    assert task.billing_payer_user_id == owner.id
    await freeze_for_task(db_session, task)
    event = UsageEvent(user_id=member.id, team_id=team.id, task_run_id=task.id, billing_key="seedream", charge_fen=180, settled=False)
    db_session.add(event)
    await db_session.flush()
    await settle_task(db_session, task.id)
    assert (owner.balance_fen, owner.frozen_fen, member.balance_fen) == (1820, 0, 90000)
    assert (event.user_id, event.team_id, event.billing_payer_user_id) == (member.id, team.id, owner.id)
    assert (await db_session.get(TeamWallet, team.id)).balance_fen == 0
    entries = (await db_session.execute(select(WalletLedger).where(WalletLedger.user_id == owner.id))).scalars().all()
    assert sum(e.delta_fen for e in entries) == -180
    assert all(f"team:{team.id} actor:{member.id}" in e.note for e in entries)
    from app.api.billing import usage_events, usage_summary
    summary = await usage_summary(db=db_session, user=owner)
    assert summary["charge_fen"] == 180
    assert summary["calls"] == 1
    assert "cost_fen" not in summary
    assert (await usage_summary(db=db_session, user=member))["charge_fen"] == 0
    assert (await usage_events(page=1, page_size=20, db=db_session, user=owner))["items"][0]["id"] == event.id
    assert (await usage_events(page=1, page_size=20, db=db_session, user=member))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 500])
async def test_total_month_limit_counts_other_members_and_reservations(db_session, monkeypatch, limit):
    team, owner, member = await seed_team(db_session, limit=None)
    team.monthly_limit_fen = limit
    if limit:
        await enqueue(db_session, owner, team, monkeypatch)
    with pytest.raises(ValueError, match="团队月度消费额度"):
        await enqueue(db_session, member, team, monkeypatch)


@pytest.mark.asyncio
async def test_owner_member_limit_is_enforced_and_old_month_does_not_count(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session)
    membership = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == owner.id))).scalar_one()
    membership.monthly_limit_fen = 300
    team.monthly_limit_fen = 300
    db_session.add(TeamMonthlyUsage(team_id=team.id, user_id=owner.id, period="2000-01", spent_fen=10000, reserved_fen=10000))
    await enqueue(db_session, owner, team, monkeypatch)
    team.monthly_limit_fen = None
    with pytest.raises(ValueError, match="成员月度消费额度"):
        await enqueue(db_session, owner, team, monkeypatch)


@pytest.mark.asyncio
async def test_transfer_does_not_change_accepted_payer(db_session, monkeypatch):
    from app.services.teams.management import transfer_ownership
    team, owner, member = await seed_team(db_session)
    first = await enqueue(db_session, member, team, monkeypatch)
    await transfer_ownership(db_session, team.id, owner.id, member.id)
    await freeze_for_task(db_session, first)
    assert (owner.balance_fen, member.balance_fen) == (1700, 90000)
    db_session.add(UsageEvent(user_id=member.id, task_run_id=first.id, billing_key="seedream", charge_fen=180, settled=False))
    await db_session.flush()
    await settle_task(db_session, first.id)
    second = await enqueue(db_session, owner, team, monkeypatch)
    assert (first.billing_payer_user_id, second.billing_payer_user_id) == (owner.id, member.id)
    assert (owner.balance_fen, member.balance_fen) == (1820, 90000)
    target = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == member.id))).scalar_one()
    assert target.monthly_limit_fen == 1000


@pytest.mark.asyncio
async def test_personal_and_multiple_teams_share_owner_commitments(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session, balance=700)
    await enqueue(db_session, member, team, monkeypatch)
    other = Team(name="同所有者的另一个团队", owner_user_id=owner.id)
    db_session.add(other)
    await db_session.flush()
    db_session.add(TeamMember(team_id=other.id, user_id=owner.id, role="owner"))
    await db_session.flush()
    await enqueue(db_session, owner, other, monkeypatch)
    personal = TaskRun(requested_by=owner.id, domain="tools", task_type="t2i", payload={})
    with pytest.raises(ValueError, match="余额不足"):
        await ensure_balance_for_task(db_session, owner, personal)
    with pytest.raises(ValueError, match="所有者余额不足"):
        await enqueue(db_session, member, team, monkeypatch)


@pytest.mark.asyncio
async def test_new_team_admission_respects_personal_pending_tasks(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session, balance=500)
    db_session.add(TaskRun(requested_by=owner.id, domain="tools", task_type="t2i", payload={}, status="pending", billing_status="none", billing_estimate_fen=300))
    await db_session.flush()
    with pytest.raises(ValueError, match="所有者余额不足"):
        await enqueue(db_session, member, team, monkeypatch)


@pytest.mark.asyncio
async def test_legacy_frozen_task_finishes_in_original_wallet(db_session):
    """无付款快照的旧任务不能重复扣到个人账户。"""
    team, owner, member = await seed_team(db_session)
    wallet = await db_session.get(TeamWallet, team.id)
    wallet.balance_fen, wallet.frozen_fen = 1700, 300
    task = TaskRun(requested_by=member.id, team_id=team.id, domain="drama", task_type="asset_image", payload={}, status="succeeded", billing_status="frozen", billing_estimate_fen=300, billing_period=billing_period(), billing_enabled_snapshot=True)
    db_session.add(task)
    await db_session.flush()
    db_session.add(UsageEvent(user_id=member.id, team_id=team.id, task_run_id=task.id, billing_key="seedream", charge_fen=180, settled=False))
    await db_session.flush()
    await settle_task(db_session, task.id)
    assert (wallet.balance_fen, wallet.frozen_fen, owner.balance_fen) == (1820, 0, 2000)
