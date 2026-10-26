"""真实旧漫剧路由与团队租约、空间隔离的集成回归。"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.drama import router as drama_router
from app.api.team_edit_locks import router as locks_router
from app.models import UsageEvent
from app.models_drama import DramaAsset, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.models_teams import TeamMember
from tests.test_teams_api import teams_api  # noqa: F401


@pytest.mark.asyncio
async def test_edit_lock_conflict_returns_serializable_423_instead_of_network_error(drama_api):
    a = drama_api
    await headers(a, "asset", a.asset.id, "owner-holding")
    a.auth["user"] = a.member
    response = await a.client.post(f"/api/teams/{a.team_id}/edit-locks/acquire", json={
        "project_id": a.project.id, "resource_type": "asset", "resource_id": a.asset.id,
        "session_id": "member-conflict",
    })
    assert response.status_code == 423, response.text
    detail = response.json()["detail"]
    assert detail["owner_user_id"] == a.owner.id
    assert "正在编辑" in detail["message"]
    assert isinstance(detail["expires_at"], str)
    assert "token" not in detail


@pytest.mark.asyncio
async def test_other_editor_lock_does_not_block_reading_storyboard(drama_api):
    from app.models_drama import DramaEpisode, DramaEpisodeFragment
    a = drama_api
    episode = DramaEpisode(project_id=a.project.id, name="只读分镜", params={"episodeNumber": 1})
    a.db.add(episode)
    await a.db.flush()
    a.db.add(DramaEpisodeFragment(episode_id=episode.id, sort_order=0, content="可读取的分镜正文", video="/static/mock/local/default.mp4"))
    await a.db.commit()
    await headers(a, "episode", 1, "owner-editing")
    a.auth["user"] = a.member
    locked = await a.client.post(f"/api/teams/{a.team_id}/edit-locks/acquire", json={"project_id": a.project.id, "resource_type": "episode", "resource_id": 1, "session_id": "member-viewing"})
    assert locked.status_code == 423
    listing = await a.client.get(f"/api/drama/episodes?project_id={a.project.id}")
    assert listing.status_code == 200
    result = await a.client.get(f"/api/drama/episodes/{episode.id}")
    assert result.status_code == 200, result.text
    assert result.json()["fragments"][0]["content"] == "可读取的分镜正文"
    assert result.json()["fragments"][0]["video"] == "/static/mock/local/default.mp4"


@pytest.mark.asyncio
async def test_team_status_repairs_cancelled_queue_without_touching_other_members_active_task(drama_api):
    """团队取消任务后不再假排队，查看者不是提交者时也保留真正的活动任务。"""
    from app.models_drama import DramaEpisode, DramaEpisodeFragment
    a = drama_api
    episode = DramaEpisode(project_id=a.project.id, name="状态修复测试")
    a.db.add(episode)
    await a.db.flush()
    cancelled = DramaEpisodeFragment(episode_id=episode.id, sort_order=0, params={"generation": {"status": "queued", "message": "已入队"}})
    active = DramaEpisodeFragment(episode_id=episode.id, sort_order=1, params={"generation": {"status": "queued"}})
    a.db.add_all([cancelled, active])
    await a.db.flush()
    for fragment, state in [(cancelled, "cancelled"), (active, "pending")]:
        a.db.add(TaskRun(domain="drama", task_type="fragment_video", queue_type="video", status=state,
                        requested_by=a.owner.id, team_id=a.team_id, drama_project_id=a.project.id,
                        episode_id=episode.id, fragment_id=fragment.id,
                        payload={"fragment_ids": [fragment.id]}))
    await a.db.commit()
    a.auth["user"] = a.member
    response = await a.client.get(f"/api/drama/episodes/{episode.id}/generate_status")
    assert response.status_code == 200, response.text
    data = response.json()
    statuses = {row["fragment_id"]: row["status"] for row in data["fragments"]}
    assert statuses == {cancelled.id: "cancelled", active.id: "queued"}
    assert data["running"] == 1
    await a.db.refresh(cancelled)
    assert cancelled.params["generation"]["status"] == "cancelled"
    assert active.params["generation"]["status"] == "queued"


@pytest_asyncio.fixture
async def drama_api(teams_api):
    """挂载生产依赖，所有请求只访问当前 PostgreSQL 回滚事务。"""
    a = teams_api
    a.app.include_router(drama_router, prefix="/api")
    a.app.include_router(locks_router, prefix="/api")
    row = DramaProject(user_id=a.owner.id, team_id=a.team_id, title="团队作品")
    a.db.add(row)
    await a.db.flush()
    a.db.add(DramaScript(project_id=row.id, name="剧本", episode_content={"episodes": [{"episodeNumber": 1, "body": "第一集"}, {"episodeNumber": 2, "body": "第二集"}]}))
    asset = DramaAsset(project_id=row.id, type="character", asset_type="image", name="最新角色", params={"description": "最新描述"})
    a.db.add(asset)
    await a.db.commit()
    a.project, a.asset = row, asset
    return a


async def headers(a, kind, resource_id=0, session="editor", force=False):
    """由真实 HTTP 接口获取不可伪造的编辑凭证。"""
    response = await a.client.post(f"/api/teams/{a.team_id}/edit-locks/acquire", json={"project_id": a.project.id, "resource_type": kind, "resource_id": resource_id, "session_id": session, "force": force})
    assert response.status_code == 200, response.text
    value = response.json()
    return {"X-Edit-Session": session, "X-Edit-Token": value["token"], "X-Edit-Version": str(value["version"])}


@pytest.mark.asyncio
async def test_old_drama_routes_require_current_membership_and_separate_spaces(drama_api):
    """队友能读创建者的作品，个人列表不混入团队，离队创建者也被即时拒绝。"""
    a = drama_api
    a.auth["user"] = a.member
    assert (await a.client.get(f"/api/drama/projects/{a.project.id}")).status_code == 200
    assert (await a.client.get("/api/drama/projects")).json() == []
    scoped = await a.client.get("/api/drama/projects", headers={"X-Team-Id": str(a.team_id)})
    assert [row["id"] for row in scoped.json()] == [a.project.id]
    a.auth["user"] = a.outsider
    assert (await a.client.get(f"/api/drama/projects/{a.project.id}")).status_code == 404
    a.auth["user"] = a.owner
    membership = await a.db.scalar(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.owner.id))
    membership.active = False
    await a.db.commit()
    assert (await a.client.get(f"/api/drama/projects/{a.project.id}")).status_code == 404


@pytest.mark.asyncio
async def test_old_write_routes_reject_missing_wrong_and_fenced_tokens(drama_api):
    """旧页面不能绕过租约；接管后旧标签页的保存失效。"""
    a = drama_api
    path = f"/api/drama/assets/{a.asset.id}"
    assert (await a.client.patch(path, json={"name": "未授权"})).status_code == 428
    wrong = await headers(a, "episode", 1)
    assert (await a.client.patch(path, json={"name": "错目标"}, headers=wrong)).status_code == 409
    a.auth["user"] = a.member
    old = await headers(a, "asset", a.asset.id, "member")
    assert (await a.client.patch(path, json={"name": "成员保存"}, headers=old)).status_code == 200
    a.auth["user"] = a.owner
    await headers(a, "asset", a.asset.id, "owner", force=True)
    a.auth["user"] = a.member
    assert (await a.client.patch(path, json={"name": "过期保存"}, headers=old)).status_code == 409


@pytest.mark.asyncio
async def test_team_scripts_save_per_episode_and_preserve_other_writer(drama_api):
    """旧整包保存拒绝；分集提交只更新本集，版本不匹配明确冲突。"""
    a = drama_api
    script_headers = await headers(a, "script")
    assert (await a.client.patch(f"/api/drama/scripts/{a.project.id}", headers=script_headers, json={"episode_content": {"episodes": []}})).status_code == 409
    first = await headers(a, "episode", 1)
    assert (await a.client.patch(f"/api/drama/scripts/{a.project.id}/episodes/1", headers=first, json={"body": "缺少读取版本"})).status_code == 428
    a.auth["user"] = a.member
    second = await headers(a, "episode", 2, "member")
    assert (await a.client.patch(f"/api/drama/scripts/{a.project.id}/episodes/2", headers=second, json={"body": "成员第二集", "version": 0})).status_code == 200
    a.auth["user"] = a.owner
    path = f"/api/drama/scripts/{a.project.id}/episodes/1"
    assert (await a.client.patch(path, headers=first, json={"body": "所有者第一集", "version": 0})).status_code == 200
    assert (await a.client.patch(path, headers=first, json={"body": "陈旧第一集", "version": 0})).status_code == 409
    script = await a.db.scalar(select(DramaScript).where(DramaScript.project_id == a.project.id))
    assert [row["body"] for row in script.episode_content["episodes"]] == ["所有者第一集", "成员第二集"]


@pytest.mark.asyncio
async def test_running_task_cannot_be_forced_and_chat_does_not_occupy_content(drama_api):
    """系统写回占用不能被人工接管，只读聊天不会阻塞正文。"""
    a = drama_api
    task = TaskRun(domain="drama", task_type="episode_scripts", requested_by=a.member.id, drama_project_id=a.project.id, team_id=a.team_id, status="running", payload={"episode_number": 1})
    a.db.add(task)
    await a.db.commit()
    path = f"/api/teams/{a.team_id}/edit-locks/acquire"
    body = {"project_id": a.project.id, "resource_type": "episode", "resource_id": 1, "session_id": "owner", "force": True}
    assert (await a.client.post(path, json=body)).status_code == 423
    task.task_type = "agent_chat"
    await a.db.commit()
    assert (await a.client.post(path, json=body)).status_code == 200


@pytest.mark.asyncio
async def test_team_canvas_cannot_overwrite_assets_and_usage_is_private(drama_api):
    """布局快照不覆盖独立资产；普通成员不可由项目统计读取他人花费或上游成本。"""
    a = drama_api
    lock = await headers(a, "canvas")
    node = {"id": "node", "position": {"x": 10, "y": 20}, "data": {"assetId": a.asset.id, "label": "陈旧角色", "mediaUrl": "/old.png"}}
    result = await a.client.post("/api/drama/canvas", headers=lock, json={"project_id": a.project.id, "nodes": [node], "edges": []})
    assert result.status_code == 200, result.text
    await a.db.refresh(a.asset)
    assert a.asset.name == "最新角色" and a.asset.url is None
    canvas = (await a.client.get(f"/api/drama/canvas/{a.project.id}")).json()
    assert canvas["nodes"][0]["data"]["label"] == "最新角色"
    a.db.add_all([UsageEvent(user_id=a.owner.id, team_id=a.team_id, drama_project_id=a.project.id, billing_key="llm_chat", charge_fen=100, cost_fen=55), UsageEvent(user_id=a.member.id, team_id=a.team_id, drama_project_id=a.project.id, billing_key="llm_chat", charge_fen=30, cost_fen=20)])
    await a.db.commit()
    a.auth["user"] = a.member
    usage = (await a.client.get(f"/api/drama/projects/{a.project.id}")).json()["usage"]
    assert usage["charge_fen"] == 30
    assert "cost_fen" not in usage and "cost_yuan" not in usage
    a.auth["user"] = a.owner
    usage = (await a.client.get(f"/api/drama/projects/{a.project.id}")).json()["usage"]
    assert usage["charge_fen"] == 130
    assert "cost_fen" not in usage and "cost_yuan" not in usage


def test_main_registers_team_api_routes():
    """主应用确实公开了前端使用的接口，防止只在测试子应用里通过。"""
    from app.main import app
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert {"/api/teams", "/api/team-invitations/accept", "/api/teams/{team_id}/projects", "/api/teams/{team_id}/usage/summary", "/api/teams/{team_id}/edit-locks/acquire", "/api/drama/assets/{asset_id}/copy"} <= paths
