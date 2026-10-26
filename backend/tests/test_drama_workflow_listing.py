"""独立漫剧/画布列表的工作流兼容与空间隔离。"""

import pytest

from sqlalchemy import select

from app.models_drama import DramaProject, DramaScript
from app.models_tasks import TaskRun
from tests.test_team_drama_integration import drama_api  # noqa: F401
from tests.test_teams_api import teams_api  # noqa: F401


@pytest.mark.asyncio
async def test_new_drama_preserves_selected_episode_count_without_changing_legacy_projects(drama_api):
    """初始集数支持 1 至 120 集，严格拒绝非法值并保持项目与剧本一致。"""
    a = drama_api
    payload = {"title": "雨夜来信", "source": "一个年轻的邮差在雨夜收到一封写给未来自己的信，决定寻找写信的人。"}
    for episode_count in [0, 121, 1.5, "1", True]:
        response = await a.client.post("/api/drama/projects", json={**payload, "episode_count": episode_count})
        assert response.status_code == 422, response.text
    for episode_count in [1, 12, 120]:
        created = await a.client.post("/api/drama/projects", json={**payload, "episode_count": episode_count, "params": {"episode_count": 5}})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["params"]["episode_count"] == episode_count
        assert body["script"]["params"]["episode_count"] == episode_count
        assert await a.db.scalar(select(TaskRun.id).where(TaskRun.drama_project_id == body["id"])) is None
    # 通用 params 不能覆盖受控的初始集数。
    created = await a.client.post("/api/drama/projects", json={**payload, "params": {"episode_count": 12}})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["params"]["episode_count"] == 1
    assert body["script"]["params"]["episode_count"] == 1
    legacy = DramaProject(user_id=a.owner.id, title="旧多集作品", params={"workflow": "script", "episode_count": 12})
    a.db.add(legacy)
    await a.db.commit()
    loaded = await a.client.get(f"/api/drama/projects/{legacy.id}")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["params"]["episode_count"] == 12


@pytest.mark.asyncio
async def test_workflow_list_keeps_legacy_projects_and_unfiltered_contract(drama_api):
    a = drama_api
    rows = [
        DramaProject(user_id=a.owner.id, title="普通漫剧", params={"workflow": "script"}),
        DramaProject(user_id=a.owner.id, title="实验画布", params={"workflow": "canvas"}),
        DramaProject(user_id=a.owner.id, title="自由画布旧项目"),
        DramaProject(user_id=a.owner.id, title="旧空白项目"),
        DramaProject(user_id=a.owner.id, title="自由画布题材剧本", params={"workflow": "script"}),
    ]
    a.db.add_all(rows)
    await a.db.flush()
    a.db.add(DramaScript(project_id=rows[3].id, name="旧脚本", source="自由画布创作项目，稍后完善。"))
    await a.db.commit()

    all_projects = await a.client.get("/api/drama/projects", params={"page_size": 50})
    assert all_projects.status_code == 200, all_projects.text
    assert {item["id"] for item in all_projects.json()["items"]} == {row.id for row in rows}
    for workflow, expected in [
        ("script", {rows[0].id, rows[4].id}),
        ("canvas", {rows[1].id, rows[2].id, rows[3].id}),
    ]:
        response = await a.client.get("/api/drama/projects", params={"workflow": workflow})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {item["id"] for item in items} == expected
        assert all(item["workflow"] == workflow for item in items)
    assert (await a.client.get("/api/drama/projects?workflow=unknown")).status_code == 422


@pytest.mark.asyncio
async def test_project_list_paging_status_and_search(drama_api):
    a = drama_api
    rows = [
        DramaProject(user_id=a.owner.id, title=f"漫剧{i}", params={"workflow": "script"})
        for i in range(7)
    ]
    a.db.add_all(rows)
    await a.db.commit()

    first = await a.client.get("/api/drama/projects", params={"workflow": "script", "page": 1, "page_size": 6})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["meta"] == {"page": 1, "page_size": 6, "total": 7}
    assert len(body["items"]) == 6
    second = await a.client.get("/api/drama/projects", params={"workflow": "script", "page": 2, "page_size": 6})
    items2 = second.json()["items"]
    assert len(items2) == 1
    assert {item["id"] for item in body["items"] + items2} == {row.id for row in rows}
    # 越界页码收口到末页
    clamped = await a.client.get("/api/drama/projects", params={"workflow": "script", "page": 9, "page_size": 6})
    assert clamped.json()["meta"]["page"] == 2
    # 无剧本的 script 项目全部命中 draft
    drafts = await a.client.get("/api/drama/projects", params={"workflow": "script", "status": "draft"})
    assert drafts.json()["meta"]["total"] == 7
    # 标题搜索按字面匹配
    found = await a.client.get("/api/drama/projects", params={"q": "漫剧3"})
    assert found.json()["meta"]["total"] == 1


@pytest.mark.asyncio
async def test_workflow_filter_cannot_cross_personal_or_team_space(drama_api):
    a = drama_api
    personal = DramaProject(user_id=a.owner.id, title="个人画布", params={"workflow": "canvas"})
    team_canvas = DramaProject(user_id=a.owner.id, team_id=a.team_id, title="团队画布", params={"workflow": "canvas"})
    outsider_canvas = DramaProject(user_id=a.outsider.id, title="他人画布", params={"workflow": "canvas"})
    a.db.add_all([personal, team_canvas, outsider_canvas])
    await a.db.commit()

    mine = await a.client.get("/api/drama/projects?workflow=canvas")
    assert [row["id"] for row in mine.json()["items"]] == [personal.id]
    a.auth["user"] = a.member
    assert (await a.client.get("/api/drama/projects?workflow=canvas")).json()["items"] == []
    headers = {"X-Team-Id": str(a.team_id)}
    team = await a.client.get("/api/drama/projects?workflow=canvas", headers=headers)
    assert team.status_code == 200, team.text
    assert [row["id"] for row in team.json()["items"]] == [team_canvas.id]
    a.auth["user"] = a.outsider
    assert (await a.client.get("/api/drama/projects?workflow=canvas", headers=headers)).status_code == 404
