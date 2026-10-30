"""团队内容 API：项目归属迁入和本地媒体独立复制的真实 PostgreSQL 测试。"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.team_content import personal_router, router
from app.models import UsageEvent
from app.models_drama import DramaAsset, DramaEpisode, DramaProject, DramaScript
from app.models_team_locks import TeamEditLock
from app.models_tasks import TaskRun
from app.models_teams import Team, TeamMember
from app.services.teams.edit_locks import acquire_lock
from tests.test_teams_api import teams_api  # noqa: F401


@pytest_asyncio.fixture
async def content_api(teams_api, monkeypatch, tmp_path):
    """复用认证/事务 fixture，所有媒体读写仅进入 pytest 临时目录。"""
    teams_api.app.include_router(router, prefix="/api")
    teams_api.app.include_router(personal_router, prefix="/api")
    monkeypatch.setattr("app.services.storage.STATIC_ROOT", tmp_path)
    monkeypatch.setattr("app.services.storage.GENERATED_ROOT", tmp_path / "generated")
    teams_api.static_root = tmp_path
    return teams_api


async def project(a, *, team_id=None, user_id=None, title="项目"):
    """创建不触发模型任务的最小漫剧项目。"""
    row = DramaProject(user_id=user_id or a.owner.id, team_id=team_id, title=title, params={"episode_count": 2})
    a.db.add(row)
    await a.db.flush()
    return row


async def copy_headers(a, target):
    """获取真实目标 asset:0 租约供资产复制接口复核。"""
    lock = await acquire_lock(a.db, a.team_id, a.owner.id, target.id, "asset", 0, "copy-test")
    return {"X-Edit-Session": "copy-test", "X-Edit-Token": lock["token"], "X-Edit-Version": str(lock["version"])}


@pytest.mark.asyncio
async def test_team_content_lists_are_paginated_and_scoped(content_api):
    """团队列表排除个人和其他团队，搜索/类型/项目筛选在服务端生效。"""
    a = content_api
    first = await project(a, team_id=a.team_id, title="长安故事")
    await project(a, team_id=a.team_id, title="夏日")
    await project(a, title="个人长安")
    a.db.add(DramaAsset(project_id=first.id, type="character", asset_type="image", name="长安角色", params={"description": "角色介绍"}))
    await a.db.commit()
    response = await a.client.get(f"/api/teams/{a.team_id}/projects", params={"q": "长安", "page_size": 1})
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["total"] == 1
    assert response.json()["items"][0]["id"] == first.id
    assert response.json()["items"][0]["status"] == "draft"
    response = await a.client.get(f"/api/teams/{a.team_id}/assets", params={"type": "character", "project_id": first.id, "q": "长安"})
    assert response.json()["meta"]["total"] == 1
    assert response.json()["items"][0]["description"] == "角色介绍"
    a.auth["user"] = a.outsider
    assert (await a.client.get(f"/api/teams/{a.team_id}/projects")).status_code == 404
    assert (await a.client.get(f"/api/teams/{a.team_id}/assets")).status_code == 404


@pytest.mark.asyncio
async def test_team_content_create_requires_real_source(content_api):
    """创建沿用真实漫剧数据结构；短创意被拒绝，不伪造创意或启动生成。"""
    a = content_api
    path = f"/api/teams/{a.team_id}/projects"
    assert (await a.client.post(path, json={"title": "测试", "source": "太短", "episode_count": 1})).status_code == 422
    source = "这是由用户亲自提供的一段足够长的原始创意，用于创建真实团队项目。"
    for episode_count in [0, 121, 1.5, "1", True]:
        invalid = await a.client.post(path, json={"title": "新作品", "source": source, "episode_count": episode_count})
        assert invalid.status_code == 422, invalid.text
    for requested_count in [None, 3, 120]:
        payload = {"title": "新作品", "source": source}
        if requested_count is not None:
            payload["episode_count"] = requested_count
        created = await a.client.post(path, json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["team_id"] == a.team_id
        assert body["script"]["source"] == source
        assert body["params"]["episode_count"] == (requested_count or 1)
        assert body["script"]["params"]["episode_count"] == (requested_count or 1)
        assert await a.db.scalar(select(TaskRun.id).where(TaskRun.drama_project_id == body["id"])) is None


@pytest.mark.asyncio
async def test_team_content_transfer_refuses_busy_then_preserves_id_and_old_costs(content_api):
    """迁入仅允许本人空闲项目，保留项目 ID、历史个人任务和费用归属。"""
    a = content_api
    row = await project(a, title="待迁入")
    script = DramaScript(project_id=row.id, name="剧本", episode_content={"episodes": [{"body": "第一集"}, {"episodeNumber": 1, "body": "第二集"}, {"episodeNumber": 1, "body": "第三集"}]})
    a.db.add(script)
    task = TaskRun(domain="drama", task_type="script_summary", requested_by=a.owner.id, drama_project_id=row.id, status="pending", billing_status="none")
    usage = UsageEvent(user_id=a.owner.id, drama_project_id=row.id, billing_key="llm_chat", charge_fen=123, cost_fen=123, settled=True)
    a.db.add_all([task, usage])
    await a.db.commit()
    listing = await a.client.get(f"/api/teams/{a.team_id}/transferable-projects")
    assert listing.json()["items"][0]["transferable"] is False
    path = f"/api/teams/{a.team_id}/projects/{row.id}/transfer"
    assert (await a.client.post(path)).status_code == 409
    task.status = "succeeded"
    await a.db.commit()
    transferred = await a.client.post(path)
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["id"] == row.id
    assert transferred.json()["team_id"] == a.team_id
    await a.db.refresh(task)
    await a.db.refresh(usage)
    assert task.team_id is None and usage.team_id is None and usage.charge_fen == 123
    await a.db.refresh(script)
    rows = script.episode_content["episodes"]
    assert len({item["episodeNumber"] for item in rows}) == 3
    assert [item["body"] for item in rows] == ["第一集", "第二集", "第三集"]
    assert (await a.client.post(path)).status_code == 200


@pytest.mark.asyncio
async def test_team_content_transfer_foreign_team_and_live_lease_are_rejected(content_api):
    """不可迁入别人或其他团队的项目，存活人工租约同样阻止迁入。"""
    a = content_api
    foreign = await project(a, user_id=a.outsider.id)
    assert (await a.client.post(f"/api/teams/{a.team_id}/projects/{foreign.id}/transfer")).status_code == 404
    other = Team(name="其他团队", owner_user_id=a.owner.id)
    a.db.add(other)
    await a.db.flush()
    a.db.add(TeamMember(team_id=other.id, user_id=a.owner.id, role="owner"))
    owned_elsewhere = await project(a, team_id=other.id)
    leased = await project(a)
    a.db.add(TeamEditLock(team_id=a.team_id, project_id=leased.id, resource_type="structure", resource_id=0, user_id=a.owner.id, session_id="old", token_hash="0" * 64, version=1, expires_at=datetime.now(UTC) + timedelta(minutes=1)))
    await a.db.commit()
    assert (await a.client.post(f"/api/teams/{a.team_id}/projects/{owned_elsewhere.id}/transfer")).status_code == 409
    assert (await a.client.post(f"/api/teams/{a.team_id}/projects/{leased.id}/transfer")).status_code == 409


@pytest.mark.asyncio
async def test_team_content_copy_is_independent_and_cleans_source_runtime_params(content_api):
    """复制创建新资产 ID、独立本地媒体并清除生成状态与旧画布/资源关联。"""
    a = content_api
    source, target = await project(a, team_id=a.team_id), await project(a, team_id=a.team_id)
    media = a.static_root / "generated" / f"p{source.id}" / "portrait.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"test-local-media")
    source_url = f"/static/generated/p{source.id}/portrait.png"
    asset = DramaAsset(project_id=source.id, type="character", asset_type="image", name="角色", cover=source_url, url=source_url, params={"description": "保留设定", "generation": {"status": "running"}, "canvas_node_id": "old", "project_id": source.id, "reference_asset_ids": [999], "image_versions": [{"url": source_url}]})
    a.db.add(asset)
    await a.db.commit()
    path = f"/api/teams/{a.team_id}/assets/{asset.id}/copy"
    assert (await a.client.post(path, json={"project_id": target.id})).status_code == 428
    response = await a.client.post(path, json={"project_id": target.id}, headers=await copy_headers(a, target))
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["id"] != asset.id and copied["project_id"] == target.id
    assert copied["params"]["description"] == "保留设定"
    assert set(copied["params"]).isdisjoint({"generation", "canvas_node_id", "project_id", "reference_asset_ids"})
    assert f"/p{target.id}/" in copied["url"] and copied["cover"] == copied["url"]
    destination = a.static_root / copied["url"].removeprefix("/static/")
    assert destination.read_bytes() == b"test-local-media"
    media.unlink()
    assert destination.exists()
    assert copied["params"]["image_versions"][0]["url"] == copied["url"]


@pytest.mark.asyncio
async def test_team_content_copy_cross_team_or_uncontrolled_url_rejected(content_api):
    """复制不下载任意外网 URL，源与目标必须同属当前团队。"""
    a = content_api
    target = await project(a, team_id=a.team_id)
    personal = await project(a)
    own = await project(a, team_id=a.team_id)
    foreign = DramaAsset(project_id=personal.id, type="character", name="个人素材")
    remote = DramaAsset(project_id=own.id, type="scene", name="远程素材", url="http://127.0.0.1:1234/private")
    a.db.add_all([foreign, remote])
    await a.db.commit()
    headers = await copy_headers(a, target)
    path = f"/api/teams/{a.team_id}/assets"
    assert (await a.client.post(f"{path}/{foreign.id}/copy", json={"project_id": target.id}, headers=headers)).status_code == 404
    assert (await a.client.post(f"{path}/{remote.id}/copy", json={"project_id": target.id}, headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_team_content_descendant_tasks_only_block_their_own_project(content_api):
    """历史仅关联子资产的任务不能漏判，也不能污染其他个人项目的迁入状态。"""
    a = content_api
    busy = await project(a, title="繁忙")
    idle = await project(a, title="空闲")
    asset = DramaAsset(project_id=busy.id, type="character", name="占用资产")
    a.db.add(asset)
    await a.db.flush()
    a.db.add(TaskRun(domain="drama", task_type="asset_image", requested_by=a.owner.id, asset_id=asset.id, status="running", billing_status="none"))
    await a.db.commit()
    result = (await a.client.get(f"/api/teams/{a.team_id}/transferable-projects")).json()
    rows = {row["id"]: row for row in result["items"]}
    assert rows[busy.id]["transferable"] is False
    assert rows[idle.id]["transferable"] is True
    assert (await a.client.post(f"/api/teams/{a.team_id}/projects/{busy.id}/transfer")).status_code == 409


@pytest.mark.asyncio
async def test_team_content_personal_copy_requires_both_projects_owned_by_current_user(content_api):
    """个人复制不需要团队租约，但不能读取其他用户的个人资产或媒体文件。"""
    a = content_api
    source, target, foreign = await project(a), await project(a), await project(a, user_id=a.outsider.id)
    asset = DramaAsset(project_id=source.id, type="prop", name="个人道具", params={"description": "副本内容"})
    foreign_asset = DramaAsset(project_id=foreign.id, type="prop", name="他人素材")
    a.db.add_all([asset, foreign_asset])
    await a.db.commit()
    path = "/api/drama/assets"
    result = await a.client.post(f"{path}/{asset.id}/copy", json={"project_id": target.id})
    assert result.status_code == 201, result.text
    assert result.json()["id"] != asset.id
    assert (await a.client.post(f"{path}/{foreign_asset.id}/copy", json={"project_id": target.id})).status_code == 404
    assert (await a.client.post(f"{path}/{asset.id}/copy", json={"project_id": foreign.id})).status_code == 404
    foreign_dir = a.static_root / "generated" / f"p{foreign.id}"
    foreign_dir.mkdir(parents=True)
    (foreign_dir / "secret.png").write_bytes(b"private")
    asset.url = f"/static/generated/p{foreign.id}/secret.png"
    await a.db.commit()
    assert (await a.client.post(f"{path}/{asset.id}/copy", json={"project_id": target.id})).status_code == 409


@pytest.mark.asyncio
async def test_team_content_project_status_filters_and_legacy_params(content_api):
    """完成状态来自真实视频，不因畸形旧 episode_count 让整个列表崩溃。"""
    from app.models_drama import DramaEpisodeFragment

    a = content_api
    completed = await project(a, team_id=a.team_id)
    completed.params = {"episode_count": 1}
    episode = DramaEpisode(project_id=completed.id, name="第一集")
    a.db.add(episode)
    await a.db.flush()
    a.db.add(DramaEpisodeFragment(episode_id=episode.id, sort_order=1, video="https://storage.example.com/video.mp4"))
    malformed = await project(a, team_id=a.team_id)
    malformed.params = {"episode_count": "unknown"}
    await a.db.commit()
    result = await a.client.get(f"/api/teams/{a.team_id}/projects", params={"status": "completed"})
    assert result.status_code == 200, result.text
    assert [row["id"] for row in result.json()["items"]] == [completed.id]
