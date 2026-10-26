"""团队独立调账已退役；管理员改为设置两级月度上限。"""
import uuid
import pytest
from sqlalchemy import select
from app.models_teams import TeamMember
from app.models_team_billing import TeamWallet, TeamWalletLedger, TeamMonthlyUsage
from tests.test_admin_teams import bearer, team_admin_api  # noqa: F401


@pytest.mark.asyncio
async def test_team_adjustment_retired_preserves_all_wallets(team_admin_api):
    """旧客户端不得通过团队入口调整任何账户资金。"""
    a = team_admin_api
    wallet = await a.db.get(TeamWallet, a.first.id)
    before = (a.owner.balance_fen, wallet.balance_fen, wallet.frozen_fen)
    r = await a.client.post(f"/api/admin/teams/{a.first.id}/balance-adjustments",
        json={"delta_fen": 500, "note": "旧入口", "request_id": str(uuid.uuid4())}, headers=bearer(a.admin))
    assert r.status_code == 410
    assert (a.owner.balance_fen, wallet.balance_fen, wallet.frozen_fen) == before


@pytest.mark.asyncio
async def test_admin_budget_can_set_total_and_owner_member_limit(team_admin_api):
    """总上限与成员上限相互独立，不重置消费、冻结或预留。"""
    a = team_admin_api
    url = f"/api/admin/teams/{a.first.id}"
    h = bearer(a.admin)
    before = a.owner.balance_fen
    for cap in (500, 0, None):
        r = await a.client.patch(url + "/budget", json={"monthly_limit_fen": cap}, headers=h)
        assert r.status_code == 200 and a.first.monthly_limit_fen == cap
    r = await a.client.patch(url + f"/members/{a.owner.id}/budget", json={"monthly_limit_fen": 100}, headers=h)
    assert r.status_code == 200
    m = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.first.id, TeamMember.user_id == a.owner.id))).scalar_one()
    assert m.monthly_limit_fen == 100
    u = (await a.db.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.team_id == a.first.id, TeamMonthlyUsage.user_id == a.owner.id))).scalar_one()
    assert (u.spent_fen, u.reserved_fen, a.owner.balance_fen) == (110, 20, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 1.5, True, "100", 100000001])
async def test_budget_rejects_invalid_amounts(team_admin_api, value):
    a = team_admin_api
    for suffix in ("/budget", f"/members/{a.owner.id}/budget"):
        r = await a.client.patch(f"/api/admin/teams/{a.first.id}" + suffix, json={"monthly_limit_fen": value}, headers=bearer(a.admin))
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_budget_permissions_and_inactive_member(team_admin_api):
    a = team_admin_api
    url = f"/api/admin/teams/{a.first.id}"
    for suffix in ("/budget", f"/members/{a.owner.id}/budget"):
        assert (await a.client.patch(url + suffix, json={"monthly_limit_fen": 0}, headers=bearer(a.owner))).status_code == 403
        assert (await a.client.patch(url + suffix, json={"monthly_limit_fen": 0})).status_code == 401
    assert (await a.client.patch(url + f"/members/{a.former.id}/budget", json={"monthly_limit_fen": 0}, headers=bearer(a.admin))).status_code == 404
    assert (await a.client.patch("/api/admin/teams/987654321/budget", json={"monthly_limit_fen": 0}, headers=bearer(a.admin))).status_code == 404


@pytest.mark.asyncio
async def test_legacy_team_ledger_remains_readable(team_admin_api):
    """旧账目只读保留，操作者与备注仍可追溯。"""
    a = team_admin_api
    a.db.add(TeamWalletLedger(team_id=a.first.id, actor_user_id=a.admin.id, delta_fen=500, balance_after_fen=4321, kind="adjust", ref_type="admin_adjust", ref_id=str(uuid.uuid4()), note="历史补款"))
    await a.db.flush()
    r = await a.client.get(f"/api/admin/teams/{a.first.id}/wallet-ledger", params={"q": "历史补款", "adjustments_only": True}, headers=bearer(a.admin))
    assert r.status_code == 200 and r.json()["meta"]["total"] == 1
    assert r.json()["items"][0]["actor_user_id"] == a.admin.id
