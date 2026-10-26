"""管理端团队只读接口：平台权限、聚合、历史成员、消费隔离和分页。"""
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app.api.admin import router
from app.database import get_db
from app.models import UsageEvent
from app.models_drama import DramaProject
from app.models_teams import Team, TeamMember
from app.models_team_billing import TeamMonthlyUsage, TeamWallet
from app.services.auth import create_access_token
from app.services.billing.team import billing_period
from tests.conftest import make_user


@pytest_asyncio.fixture
async def team_admin_api(db_session):
    """隔离测试库构造两个团队及个人消费，保留真实管理员鉴权链。"""
    db = db_session
    admin, owner, former, other = [await make_user(db) for _ in range(4)]
    admin.role = "admin"
    owner.nickname = "团队所有者"
    former.nickname = "历史创作者"
    prefix = "admin-teams-" + uuid.uuid4().hex[:8]
    first = Team(name=prefix + "-A", owner_user_id=owner.id)
    second = Team(name=prefix + "-B", owner_user_id=other.id)
    db.add_all([first, second])
    await db.flush()
    db.add_all([
        TeamMember(team_id=first.id, user_id=owner.id, role="owner"),
        TeamMember(team_id=first.id, user_id=former.id, role="member", active=False, monthly_limit_fen=500),
        TeamMember(team_id=second.id, user_id=other.id, role="owner"),
        TeamWallet(team_id=first.id, balance_fen=4321, frozen_fen=100),
        TeamMonthlyUsage(team_id=first.id, user_id=owner.id, period=billing_period(), spent_fen=110, reserved_fen=20),
        TeamMonthlyUsage(team_id=first.id, user_id=former.id, period=billing_period(), spent_fen=30, reserved_fen=0),
        TeamMonthlyUsage(team_id=first.id, user_id=former.id, period="2000-01", spent_fen=60, reserved_fen=0),
    ])
    project = DramaProject(team_id=first.id, user_id=owner.id, title="团队测试剧本")
    db.add(project)
    await db.flush()
    events = [
        UsageEvent(team_id=first.id, user_id=former.id, drama_project_id=project.id,
                   billing_key="drama_text", model="team-model", charge_fen=30, cost_fen=12, settled=True,
                   created_at=datetime(2026, 10, 21, 16, 0, tzinfo=UTC)),
        UsageEvent(team_id=first.id, user_id=owner.id, drama_project_id=987654321,
                   billing_key="drama_text", model="pending-model", charge_fen=90, settled=False,
                   created_at=datetime(2026, 10, 22, 15, 59, 59, 999999, tzinfo=UTC)),
        UsageEvent(team_id=first.id, user_id=owner.id, billing_key="drama_text", charge_fen=7, settled=True,
                   created_at=datetime(2026, 10, 22, 16, 0, tzinfo=UTC)),
        UsageEvent(team_id=None, user_id=former.id, billing_key="drama_text", charge_fen=9999, settled=True),
        UsageEvent(team_id=second.id, user_id=other.id, billing_key="drama_text", charge_fen=8888, settled=True),
    ]
    db.add_all(events)
    await db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def override_db():
        """API 与 fixture 共用可回滚的会话。"""
        yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, db=db, admin=admin, owner=owner, former=former,
                              first=first, second=second, prefix=prefix, events=events)


def bearer(user):
    """为测试账号签发正常 JWT，不绕过管理员依赖。"""
    return {"Authorization": "Bearer " + create_access_token(str(user.id))}


@pytest.mark.asyncio
async def test_team_list_aggregates_without_join_multiplication(team_admin_api):
    """多个成员和月份不会重复计数，空钱包只读返回零。"""
    a = team_admin_api
    r = await a.client.get("/api/admin/teams", params={"q": a.prefix}, headers=bearer(a.admin))
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 2
    rows = {row["id"]: row for row in body["items"]}
    first = rows[a.first.id]
    assert (first["member_count"], first["project_count"]) == (1, 1)
    assert (first["balance_fen"], first["frozen_fen"]) == (a.owner.balance_fen, a.owner.frozen_fen)
    assert (first["month_spent_fen"], first["total_spent_fen"]) == (140, 200)
    assert first["owner_email"] == a.owner.email
    assert rows[a.second.id]["balance_fen"] == 100000
    assert await a.db.get(TeamWallet, a.second.id) is None
    assert "hashed_password" not in str(body) and "token_hash" not in str(body)


@pytest.mark.asyncio
async def test_team_list_search_and_server_pagination(team_admin_api):
    """支持团队/所有者搜索以及服务端总数与稳定分页。"""
    a = team_admin_api
    headers = bearer(a.admin)
    for query in (str(a.first.id), a.owner.email, a.first.name):
        r = await a.client.get("/api/admin/teams", params={"q": query, "owner_id": a.owner.id}, headers=headers)
        assert [x["id"] for x in r.json()["items"]] == [a.first.id]
    r = await a.client.get("/api/admin/teams", params={"q": a.prefix, "page": 2, "page_size": 1}, headers=headers)
    assert r.json()["meta"] == {"page": 2, "page_size": 1, "total": 2}
    assert r.json()["items"][0]["id"] == a.first.id


@pytest.mark.asyncio
async def test_admin_outside_team_can_read_detail_and_missing_is_404(team_admin_api):
    """平台管理员无需加入团队；不存在的团队在全部子入口统一 404。"""
    a = team_admin_api
    headers = bearer(a.admin)
    r = await a.client.get(f"/api/admin/teams/{a.first.id}", headers=headers)
    assert r.status_code == 200 and r.json()["id"] == a.first.id
    for suffix in ("", "/members", "/usage-events"):
        assert (await a.client.get("/api/admin/teams/987654321" + suffix, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_team_members_include_removed_and_preserve_monthly_usage(team_admin_api):
    """历史成员可查，退出不会清掉历史消费；月份、角色、状态与搜索均生效。"""
    a = team_admin_api
    url = f"/api/admin/teams/{a.first.id}/members"
    headers = bearer(a.admin)
    r = await a.client.get(url, params={"state": "all", "page_size": 1}, headers=headers)
    assert r.json()["meta"]["total"] == 2 and len(r.json()["items"]) == 1
    r = await a.client.get(url, params={"state": "removed", "q": "历史", "role": "member"}, headers=headers)
    row = r.json()["items"][0]
    assert row["user_id"] == a.former.id and row["active"] is False
    assert row["spent_fen"] == 30 and row["monthly_limit_fen"] == 500
    r = await a.client.get(url, params={"state": "removed", "period": "2000-01"}, headers=headers)
    assert r.json()["items"][0]["spent_fen"] == 60
    assert (await a.client.get(url, params={"state": "active"}, headers=headers)).json()["meta"]["total"] == 1


@pytest.mark.asyncio
async def test_team_usage_isolation_dates_filters_and_totals(team_admin_api):
    """个人/其他团队消费被隔离；上海整日包含最后一秒小数，汇总不随分页改变。"""
    a = team_admin_api
    url = f"/api/admin/teams/{a.first.id}/usage-events"
    headers = bearer(a.admin)
    r = await a.client.get(url, params={"date_from": "2026-10-22", "date_to": "2026-10-22", "page_size": 1}, headers=headers)
    body = r.json()
    assert body["meta"]["total"] == 2
    assert body["summary"]["settled_charge_fen"] == 30
    assert body["summary"]["record_count"] == 2
    assert body["items"][0]["project_title"] is None
    assert "raw_usage_json" not in str(body)
    r = await a.client.get(url, params={"q": "team-model", "user_id": a.former.id, "settled": True}, headers=headers)
    row = r.json()["items"][0]
    assert r.json()["meta"]["total"] == 1 and row["charge_fen"] == 30
    assert row["project_title"] == "团队测试剧本" and row["user_email"] == a.former.email
    r = await a.client.get(url, params={"settled": False}, headers=headers)
    assert r.json()["summary"]["settled_charge_fen"] == 0
    assert r.json()["items"][0]["id"] == a.events[1].id
    r = await a.client.get(url, params={"date_to": "9999-12-31"}, headers=headers)
    assert r.status_code == 200 and r.json()["meta"]["total"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/{id}", "/{id}/members", "/{id}/usage-events"])
async def test_team_admin_routes_enforce_platform_admin(team_admin_api, suffix):
    """未登录为401，团队所有者仍不能越过平台管理员权限。"""
    a = team_admin_api
    url = "/api/admin/teams" + suffix.format(id=a.first.id)
    assert (await a.client.get(url)).status_code == 401
    assert (await a.client.get(url, headers=bearer(a.owner))).status_code == 403


@pytest.mark.asyncio
async def test_admin_team_filters_validate_input(team_admin_api):
    """拒绝倒置日期、非法角色/月份与越界分页。"""
    a = team_admin_api
    headers = bearer(a.admin)
    for path, params in [
        ("/api/admin/teams", {"page_size": 101}),
        ("/api/admin/teams", {"owner_id": 999999999999}),
        ("/api/admin/teams/999999999999", {}),
        (f"/api/admin/teams/{a.first.id}/members", {"role": "root"}),
        (f"/api/admin/teams/{a.first.id}/members", {"period": "2026-13"}),
        (f"/api/admin/teams/{a.first.id}/usage-events", {"date_from": "2026-10-23", "date_to": "2026-10-22"}),
        (f"/api/admin/teams/{a.first.id}/usage-events", {"user_id": 999999999999}),
    ]:
        assert (await a.client.get(path, params=params, headers=headers)).status_code == 422
