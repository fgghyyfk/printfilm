"""团队核心 HTTP 集成测试：独立 Postgres 测试库、事务回滚、邮件永不外发。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from app.api.teams import invitation_router, router
from app.database import get_db
from app.deps import get_current_user
from app.models_team_billing import TeamMonthlyUsage, TeamWallet
from app.models_teams import Team, TeamInvitation, TeamMember
from app.services.teams.access import get_team
from app.services.billing.team import billing_period
from tests.conftest import make_user


@pytest_asyncio.fixture
async def teams_api(db_session, monkeypatch):
    """挂载真实团队路由，通过依赖覆盖共享回滚事务与可切换登录用户。"""
    owner, admin, member, outsider = [await make_user(db_session) for _ in range(4)]
    for user in (owner, admin, member, outsider):
        user.email = user.email.replace("@test.local", "@example.com")
    owner.nickname, admin.nickname, member.nickname = "所有者", "管理员", "创作者"
    await db_session.flush()
    auth = {"user": owner}
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(invitation_router, prefix="/api")

    async def session_override():
        """HTTP 请求沿用测试事务。"""
        yield db_session

    async def user_override():
        """切换当前请求的已认证用户，不产生真实登录 token。"""
        return auth["user"]

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[get_current_user] = user_override
    monkeypatch.setattr("app.services.teams.invitations.send_email", AsyncMock(return_value=False))
    removed_tasks, removed_locks = AsyncMock(), AsyncMock()
    monkeypatch.setattr("app.services.tasks.team_scope.cancel_removed_member_tasks", removed_tasks)
    monkeypatch.setattr("app.services.teams.edit_locks.release_member_locks", removed_locks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        created = await client.post("/api/teams", json={"name": "青禾影像", "description": "一起创作", "color": "#435c4b"})
        assert created.status_code == 201, created.text
        team_id = created.json()["id"]
        db_session.add_all([
            TeamMember(team_id=team_id, user_id=admin.id, role="admin", active=True),
            TeamMember(team_id=team_id, user_id=member.id, role="member", active=True),
        ])
        await db_session.commit()
        yield SimpleNamespace(db=db_session, client=client, app=app, auth=auth, owner=owner, admin=admin, member=member, outsider=outsider, team_id=team_id, removed_tasks=removed_tasks, removed_locks=removed_locks)


async def invite(api, email: str | None = None):
    """以当前操作者创建真实数据库邀请，但邮件已由 fixture 隔离。"""
    return await api.client.post(f"/api/teams/{api.team_id}/invitations", json={"email": email or api.outsider.email})


@pytest.mark.asyncio
async def test_teams_create_summary_pagination_and_wallet(teams_api):
    """创建团队同时建立所有者关系和零余额钱包；列表遵守分页。"""
    a = teams_api
    result = await a.client.get("/api/teams", params={"page_size": 1})
    assert result.status_code == 200
    data = result.json()
    assert data["meta"] == {"page": 1, "page_size": 1, "total": 1}
    assert data["items"][0]["my_role"] == "owner"
    assert data["items"][0]["viewer_id"] == a.owner.id
    assert data["items"][0]["member_count"] == 3
    wallet = await a.db.get(TeamWallet, a.team_id)
    assert wallet is None
    assert (await a.client.get("/api/teams", params={"page": 2})).json()["items"] == []


@pytest.mark.asyncio
async def test_teams_access_rechecks_membership_and_role(teams_api):
    """无成员资格返回 404；角色不足返回 403，不能用全站管理员越权。"""
    a = teams_api
    a.auth["user"] = a.outsider
    a.outsider.role = "admin"
    assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 404
    assert (await a.client.get("/api/teams")).json()["meta"]["total"] == 0
    a.auth["user"] = a.member
    assert (await a.client.patch(f"/api/teams/{a.team_id}", json={"name": "越权"})).status_code == 403
    row = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.member.id))).scalar_one()
    row.active = False
    await a.db.commit()
    assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 404


@pytest.mark.asyncio
async def test_teams_schema_and_owner_update(teams_api):
    """空名、超长文本、非法颜色及小数金额在执行前被拒绝。"""
    a = teams_api
    for patch in ({"name": "   "}, {"name": "长" * 41}, {"description": "长" * 181}, {"color": "red"}, {"name": None}):
        assert (await a.client.patch(f"/api/teams/{a.team_id}", json=patch)).status_code == 422
    result = await a.client.patch(f"/api/teams/{a.team_id}", json={"name": " 新团队 ", "description": " 新介绍 "})
    assert result.json()["name"] == "新团队"
    assert result.json()["description"] == "新介绍"
    assert (await a.client.get(f"/api/teams/{a.team_id}/activities")).json()["meta"]["total"] >= 2


@pytest.mark.asyncio
async def test_teams_member_finances_are_owner_or_self_only(teams_api):
    """非所有者仅本人获得财务数字，其他成员仍可正常查看基本信息。"""
    a = teams_api
    period = billing_period()
    a.db.add(TeamMonthlyUsage(team_id=a.team_id, user_id=a.member.id, period=period, spent_fen=321, reserved_fen=123))
    await a.db.commit()
    for viewer in (a.owner, a.admin, a.member):
        a.auth["user"] = viewer
        result = (await a.client.get(f"/api/teams/{a.team_id}/members")).json()
        rows = {item["id"]: item for item in result["items"]}
        assert result["meta"]["total"] == 3
        assert rows[a.member.id]["spent_fen"] == (321 if viewer in (a.owner, a.member) else None)
        assert rows[a.owner.id]["reserved_fen"] == (0 if viewer is a.owner else None)
    result = await a.client.get(f"/api/teams/{a.team_id}/members", params={"q": "管理员", "role": "admin", "page_size": 1})
    assert result.json()["meta"]["total"] == 1
    assert result.json()["items"][0]["id"] == a.admin.id


@pytest.mark.asyncio
async def test_teams_owner_sets_role_and_nullable_zero_limits(teams_api):
    """额度以整数分存储；0 禁止新增消费，null 不限额；管理员无权改额度。"""
    a = teams_api
    path = f"/api/teams/{a.team_id}/members/{a.member.id}"
    for amount in (-1, 0.5, True, 100_000_001):
        assert (await a.client.patch(path, json={"monthly_limit_fen": amount})).status_code == 422
    for amount in (0, 12345, None):
        response = await a.client.patch(path, json={"monthly_limit_fen": amount})
        assert response.status_code == 200, response.text
        assert response.json()["monthly_limit_fen"] == amount
    assert (await a.client.patch(path, json={"role": "admin"})).json()["role"] == "admin"
    assert (await a.client.patch(path, json={"role": None})).status_code == 422
    assert (await a.client.patch(f"/api/teams/{a.team_id}/members/{a.owner.id}", json={"role": "member"})).status_code == 409
    a.auth["user"] = a.admin
    assert (await a.client.patch(path, json={"monthly_limit_fen": 0})).status_code == 403


@pytest.mark.asyncio
async def test_teams_remove_and_leave_soft_delete_with_cleanup(teams_api):
    """移除与退出保留成员行，取消未开始任务并释放该成员编辑锁。"""
    a = teams_api
    assert (await a.client.delete(f"/api/teams/{a.team_id}/members/{a.owner.id}")).status_code == 409
    a.auth["user"] = a.admin
    assert (await a.client.delete(f"/api/teams/{a.team_id}/members/{a.owner.id}")).status_code == 403
    response = await a.client.delete(f"/api/teams/{a.team_id}/members/{a.member.id}")
    assert response.status_code == 200
    row = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.member.id))).scalar_one()
    assert row.active is False
    a.removed_tasks.assert_awaited_with(a.db, a.team_id, a.member.id)
    a.removed_locks.assert_awaited_with(a.db, a.team_id, a.member.id)
    assert (await a.client.post(f"/api/teams/{a.team_id}/leave")).status_code == 200
    assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 404


@pytest.mark.asyncio
async def test_teams_transfer_is_atomic_and_owner_authoritative(teams_api):
    """所有权转移后唯一 owner 与权威 owner_user_id 一致，旧所有者变为 admin。"""
    a = teams_api
    target_member = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.member.id))).scalar_one()
    target_member.monthly_limit_fen = 0
    await a.db.commit()
    assert (await a.client.post(f"/api/teams/{a.team_id}/leave")).status_code == 409
    assert (await a.client.post(f"/api/teams/{a.team_id}/transfer-ownership", json={"user_id": a.owner.id})).status_code == 409
    assert (await a.client.post(f"/api/teams/{a.team_id}/transfer-ownership", json={"user_id": a.outsider.id})).status_code == 404
    result = await a.client.post(f"/api/teams/{a.team_id}/transfer-ownership", json={"user_id": a.member.id})
    assert result.status_code == 200, result.text
    team = await a.db.get(Team, a.team_id, populate_existing=True)
    rows = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.team_id))).scalars().all()
    assert team.owner_user_id == a.member.id
    assert [row.user_id for row in rows if row.role == "owner"] == [a.member.id]
    assert next(row for row in rows if row.user_id == a.owner.id).role == "admin"
    assert next(row for row in rows if row.user_id == a.member.id).monthly_limit_fen == 0
    assert (await a.client.patch(f"/api/teams/{a.team_id}", json={"name": "旧所有者"})).status_code == 403


@pytest.mark.asyncio
async def test_teams_invitation_only_hash_stored_email_bound_and_single_use(teams_api):
    """邀请令牌仅哈希持久化、必须邮箱匹配、成功加入后不可重复消费。"""
    a = teams_api
    response = await invite(a)
    assert response.status_code == 201, response.text
    invitation = response.json()
    assert invitation["email_sent"] is False
    assert invitation["token"] in invitation["accept_url"]
    row = await a.db.get(TeamInvitation, invitation["invitation"]["id"])
    assert len(row.token_hash) == 64 and row.token_hash != invitation["token"]
    assert "token_hash" not in invitation["invitation"]
    a.auth["user"] = a.member
    assert (await a.client.post("/api/team-invitations/inspect", json={"token": invitation["token"]})).status_code == 403
    a.auth["user"] = a.outsider
    assert (await a.client.post("/api/team-invitations/inspect", json={"token": invitation["token"]})).json()["team_name"] == "青禾影像"
    accepted = await a.client.post("/api/team-invitations/accept", json={"token": invitation["token"]})
    assert accepted.status_code == 200
    assert accepted.json()["my_role"] == "member"
    assert (await a.client.post("/api/team-invitations/accept", json={"token": invitation["token"]})).status_code == 409


@pytest.mark.asyncio
async def test_teams_invitation_duplicate_resend_revoke_and_expiry(teams_api):
    """有效邀请不重复签发，重发轮换令牌，撤回及过期令牌不能使用。"""
    a = teams_api
    assert (await invite(a, a.member.email)).status_code == 409
    first = (await invite(a)).json()
    assert (await invite(a)).status_code == 409
    listing = (await a.client.get(f"/api/teams/{a.team_id}/invitations")).json()
    assert listing["meta"]["total"] == 1
    assert set(listing["items"][0]).isdisjoint({"token", "token_hash"})
    invite_id = first["invitation"]["id"]
    resent = await a.client.post(f"/api/teams/{a.team_id}/invitations/{invite_id}/resend")
    assert resent.status_code == 200
    assert resent.json()["token"] != first["token"]
    a.auth["user"] = a.outsider
    assert (await a.client.post("/api/team-invitations/accept", json={"token": first["token"]})).status_code == 404
    a.auth["user"] = a.owner
    assert (await a.client.post(f"/api/teams/{a.team_id}/invitations/{invite_id}/revoke")).status_code == 200
    a.auth["user"] = a.outsider
    assert (await a.client.post("/api/team-invitations/accept", json={"token": resent.json()["token"]})).status_code == 409
    a.auth["user"] = a.owner
    second = (await invite(a)).json()
    row = await a.db.get(TeamInvitation, second["invitation"]["id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await a.db.commit()
    a.auth["user"] = a.outsider
    assert (await a.client.post("/api/team-invitations/accept", json={"token": second["token"]})).status_code == 410


@pytest.mark.asyncio
async def test_teams_rejoin_preserves_limit_and_monthly_usage(teams_api):
    """软退出重新加入不得重置原有月消费或额度，重新加入角色为普通成员。"""
    a = teams_api
    row = (await a.db.execute(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.member.id))).scalar_one()
    row.monthly_limit_fen, row.active = 5000, False
    a.db.add(TeamMonthlyUsage(team_id=a.team_id, user_id=a.member.id, period=billing_period(), spent_fen=4000, reserved_fen=0))
    await a.db.commit()
    created = (await invite(a, a.member.email)).json()
    a.auth["user"] = a.member
    assert (await a.client.post("/api/team-invitations/accept", json={"token": created["token"]})).status_code == 200
    listing = (await a.client.get(f"/api/teams/{a.team_id}/members")).json()
    me = next(item for item in listing["items"] if item["id"] == a.member.id)
    assert (me["role"], me["monthly_limit_fen"], me["spent_fen"]) == ("member", 5000, 4000)


@pytest.mark.asyncio
async def test_teams_resend_expired_invite_cannot_duplicate_new_invite(teams_api):
    """过期后另发新邀请，旧邀请不能通过重发恢复出第二条有效链接。"""
    a = teams_api
    first = (await invite(a)).json()
    old = await a.db.get(TeamInvitation, first["invitation"]["id"])
    old.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await a.db.commit()
    second = (await invite(a)).json()
    path = f"/api/teams/{a.team_id}/invitations/{old.id}/resend"
    assert (await a.client.post(path)).status_code == 409
    a.auth["user"] = a.outsider
    assert (await a.client.post("/api/team-invitations/accept", json={"token": second["token"]})).status_code == 200
    a.auth["user"] = a.owner
    assert (await a.client.post(path)).status_code == 409


@pytest.mark.asyncio
async def test_teams_member_cannot_manage_invites_and_lists_require_auth(teams_api):
    """普通成员不能读邀请邮箱列表，未登录不能读任何团队接口。"""
    a = teams_api
    a.auth["user"] = a.member
    assert (await a.client.get(f"/api/teams/{a.team_id}/invitations")).status_code == 403
    assert (await invite(a)).status_code == 403
    a.app.dependency_overrides.pop(get_current_user)
    assert (await a.client.get("/api/teams")).status_code == 401


@pytest.mark.asyncio
async def test_owner_sets_total_and_own_member_budget_without_resetting_usage(teams_api):
    """所有者能设置两级额度；普通成员不能改团队上限，null 正确清除上限。"""
    a = teams_api
    url = f"/api/teams/{a.team_id}"
    for cap in (500, 0, None):
        assert (await a.client.patch(url, json={"monthly_limit_fen": cap})).status_code == 200
        team = await a.db.get(Team, a.team_id)
        assert team.monthly_limit_fen == cap
    assert (await a.client.patch(url + f"/members/{a.owner.id}", json={"monthly_limit_fen": 0})).status_code == 200
    assert (await a.client.patch(url + f"/members/{a.owner.id}", json={"role": "member"})).status_code == 409
    for cap in (-1, True, 1.5, "500", 100000001):
        assert (await a.client.patch(url, json={"monthly_limit_fen": cap})).status_code == 422
    a.auth["user"] = a.member
    assert (await a.client.patch(url, json={"monthly_limit_fen": 0})).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("actor, expected", [("admin", 403), ("member", 403), ("outsider", 404)])
async def test_delete_team_requires_owner(teams_api, actor, expected):
    a = teams_api
    a.auth["user"] = getattr(a, actor)
    response = await a.client.delete(f"/api/teams/{a.team_id}")
    assert response.status_code == expected
    a.auth["user"] = a.owner
    assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 200


@pytest.mark.asyncio
async def test_delete_team_revokes_access_invites_and_retains_history(teams_api):
    from app.models_tasks import TaskRun
    from app.models_teams import TeamActivity
    a = teams_api
    issued = await invite(a)
    assert issued.status_code == 201
    history = TaskRun(domain="drama", task_type="fragment_video", queue_type="video",
                      requested_by=a.owner.id, team_id=a.team_id, status="succeeded")
    a.db.add(history)
    await a.db.commit()
    history_id = history.id
    response = await a.client.delete(f"/api/teams/{a.team_id}")
    assert response.status_code == 204, response.text
    for user in (a.owner, a.admin, a.member):
        a.auth["user"] = user
        assert (await a.client.get("/api/teams")).json()["items"] == []
        assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 404
    assert (await a.client.delete(f"/api/teams/{a.team_id}")).status_code == 404
    invitation = await a.db.scalar(select(TeamInvitation).where(TeamInvitation.team_id == a.team_id))
    assert invitation.revoked_at is not None
    assert await a.db.get(Team, a.team_id) is not None
    assert (await a.db.get(TaskRun, history_id)).team_id == a.team_id
    assert await a.db.scalar(select(TeamActivity.id).where(TeamActivity.team_id == a.team_id, TeamActivity.action == "team.deleted"))
    assert a.removed_locks.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "awaiting_poll", "finalizing"])
async def test_delete_team_blocks_unfinished_generation(teams_api, status):
    from app.models_tasks import TaskRun
    a = teams_api
    a.db.add(TaskRun(domain="drama", task_type="fragment_video", queue_type="video",
                     requested_by=a.member.id, team_id=a.team_id, status=status))
    await a.db.commit()
    response = await a.client.delete(f"/api/teams/{a.team_id}")
    assert response.status_code == 409, response.text
    assert (await a.client.get(f"/api/teams/{a.team_id}")).status_code == 200
    a.removed_locks.assert_not_awaited()
