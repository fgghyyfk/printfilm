"""Global catalog isolation, delivery identity and media classification."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Project, Shot, Template, ToolRun
from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject
from app.models_tasks import TaskRun, TaskStep
from app.services.drama.media_library import delivered_urls, media_kind, tool_items
from tests.test_team_drama_integration import drama_api  # noqa: F401
from tests.test_teams_api import teams_api  # noqa: F401


def test_formal_outputs_and_partial_delivery_keep_distinct_identity():
    now = datetime.now(UTC)
    run = SimpleNamespace(id=4, kind="video", status="failed", prompt="实际视频提示词", urls=["https://cdn/a.mp4", "https://cdn/b.mp4"],
        preview_url="https://cdn/poster.jpg", params={"duration": "9s", "model_id": "model", "file_paths": ["private"], "api_key": "private"},
        created_at=now, updated_at=now)
    items = tool_items(run)
    assert [row.key for row in items] == ["tool:4:0", "tool:4:1"]
    assert all(row.project_id is None for row in items)
    assert items[0].url == "https://cdn/a.mp4" and items[0].cover.endswith("poster.jpg")
    assert items[0].params["media_metadata"]["parameters"]["requested_duration_sec"] == 9
    assert "duration_sec" not in items[0].params["media_metadata"]["parameters"]
    assert items[0].params["media_metadata"]["parameter_source"] == "request"
    assert "file_paths" not in str(items[0].params) and "api_key" not in str(items[0].params)
    run.urls = []
    assert tool_items(run) == []  # A poster alone is not delivered video.


def test_media_types_and_invalid_output_payloads():
    assert media_kind("image", "video") == "video"
    assert media_kind("voice", "image") == "audio"
    assert media_kind("image", "image", "/static/sample.mp4?v=2") == "video"
    assert delivered_urls({"not": "an array"}) == []
    assert delivered_urls([None, 1, "", "C:/private.png", "javascript:void(0)", "/static/a.png", "/static/a.png"]) == [(5, "/static/a.png")]


@pytest.mark.asyncio
async def test_library_scopes_unassigned_media_filters_and_pagination(drama_api):
    a = drama_api
    project = DramaProject(user_id=a.owner.id, title="自由实验", params={"workflow": "canvas"})
    foreign = DramaProject(user_id=a.outsider.id, title="他人的素材")
    a.db.add_all([project, foreign])
    await a.db.flush()
    a.db.add_all([
        DramaAsset(project_id=project.id, name="角色甲", type="character", asset_type="image", url="/static/character.png"),
        DramaAsset(project_id=project.id, name="画布视频", type="image", asset_type="video", url="/static/a.mp4"),
        DramaAsset(project_id=project.id, name="画布音频", type="audio", asset_type="audio", url="/static/a.mp3"),
        DramaAsset(project_id=project.id, name="不显示文本", type="text", asset_type="text"),
        DramaAsset(project_id=foreign.id, name="不能看", type="image", asset_type="image"),
        ToolRun(user_id=a.owner.id, tool_id="t2v", kind="video", status="failed", urls=["https://cdn/a.mp4", "https://cdn/b.mp4"]),
        ToolRun(user_id=a.owner.id, tool_id="t2i", kind="image", status="succeeded", urls=["https://cdn/a.png"]),
        ToolRun(user_id=a.owner.id, tool_id="tts", kind="audio", status="succeeded", urls=["https://cdn/a.mp3"]),
        ToolRun(user_id=a.owner.id, tool_id="t2v", kind="video", status="running", urls=[], preview_url="https://cdn/poster.png"),
        ToolRun(user_id=a.outsider.id, tool_id="t2i", kind="image", status="succeeded", urls=["https://cdn/secret.png"]),
    ])
    await a.db.commit()
    response = await a.client.get("/api/drama/library-assets")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["total"] == 7
    assert len({item["key"] for item in body["items"]}) == 7
    assert next(item for item in body["items"] if item["name"] == "画布视频")["project_url"] == f"/drama/projects/{project.id}/canvas"
    for kind, total in [("image", 2), ("video", 3), ("audio", 2), ("character", 1)]:
        result = (await a.client.get("/api/drama/library-assets", params={"type": kind})).json()
        assert result["meta"]["total"] == total
    unassigned = (await a.client.get("/api/drama/library-assets?unassigned=true&page_size=2&page=99")).json()
    assert unassigned["meta"] == {"total": 4, "total_all": 4, "page": 2, "page_size": 2}
    assert all(item["project_id"] is None for item in unassigned["items"])
    assert (await a.client.get("/api/drama/library-assets", params={"project_id": project.id})).json()["meta"]["total"] == 3
    assert (await a.client.get("/api/drama/library-assets", params={"q": "自由实验"})).json()["meta"]["total"] == 3
    assert (await a.client.get("/api/drama/library-assets?q=%25")).json()["meta"]["total"] == 0
    assert (await a.client.get("/api/drama/library-assets", params={"project_id": foreign.id})).status_code == 404
    team = (await a.client.get("/api/drama/library-assets", headers={"X-Team-Id": str(a.team_id)})).json()
    assert [row["key"] for row in team["items"]] == [f"drama:{a.asset.id}"]
    assert (await a.client.get("/api/drama/library-assets?unassigned=true", headers={"X-Team-Id": str(a.team_id)})).json()["items"] == []
    a.auth["user"] = a.outsider
    assert (await a.client.get("/api/drama/library-assets", headers={"X-Team-Id": str(a.team_id)})).status_code == 404
    for invalid in ["type=text", "media_kind=text", "page=0", "page_size=100", "project_id=0"]:
        assert (await a.client.get(f"/api/drama/library-assets?{invalid}")).status_code == 422


@pytest.mark.asyncio
async def test_media_category_and_image_subtype_intersect_before_pagination(drama_api):
    a = drama_api
    project = DramaProject(user_id=a.owner.id, title="两级筛选", params={"workflow": "canvas"})
    a.db.add(project)
    await a.db.flush()
    a.db.add_all([
        DramaAsset(project_id=project.id, name="角色图片", type="character", asset_type="image", url="/static/character.png"),
        DramaAsset(project_id=project.id, name="角色视频", type="character", asset_type="video", url="/static/character.mp4"),
        DramaAsset(project_id=project.id, name="场景图片", type="scene", asset_type="image", url="/static/scene.png"),
        DramaAsset(project_id=project.id, name="道具图片", type="prop", asset_type="image", url="/static/prop.png"),
        DramaAsset(project_id=project.id, name="普通图片", type="image", asset_type="image", url="/static/upload.png"),
        DramaAsset(project_id=project.id, name="角色音色", type="voice", asset_type="audio", url="/static/voice.mp3"),
    ])
    await a.db.commit()
    params = {"project_id": project.id, "media_kind": "image", "type": "character", "page_size": 1, "page": 99}
    response = await a.client.get("/api/drama/library-assets", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["meta"] == {"total": 1, "total_all": 6, "page": 1, "page_size": 1}
    assert [row["name"] for row in response.json()["items"]] == ["角色图片"]
    for kind, expected in [("image", 4), ("video", 1), ("audio", 1)]:
        result = (await a.client.get("/api/drama/library-assets", params={"project_id": project.id, "media_kind": kind})).json()
        assert result["meta"]["total"] == expected
        assert all(row["media_kind"] == kind for row in result["items"])
    # 隐藏的图片分类不能让声音被误归入图片，也不能绕过搜索条件。
    assert (await a.client.get("/api/drama/library-assets", params={**params, "q": "视频"})).json()["items"] == []
    assert (await a.client.get("/api/drama/library-assets", params={**params, "type": "voice"})).json()["items"] == []


async def short_project(a, *, owner=None, project_id=None):
    template = Template(id=f"library-{uuid4().hex}", name="目录测试模板", style_prefix="test")
    a.db.add(template)
    await a.db.flush()
    project = Project(user_id=(owner or a.owner).id, template_id=template.id, title="短视频目录项目",
        source_text="test", final_video_url="/static/short-final.mp4", cover_url="/static/short-cover.png")
    if project_id is not None:
        project.id = project_id
    a.db.add(project)
    await a.db.flush()
    return project


@pytest.mark.asyncio
async def test_catalog_collects_fragment_archives_compositions_and_short_video_outputs(drama_api):
    a = drama_api
    project = DramaProject(user_id=a.owner.id, title="完整视频归属")
    a.db.add(project)
    await a.db.flush()
    episode = DramaEpisode(project_id=project.id, name="第一集")
    a.db.add(episode)
    await a.db.flush()
    fragment = DramaEpisodeFragment(episode_id=episode.id, sort_order=0, video="/static/fragment.mp4",
        content="当前可编辑的分镜正文", cover="/static/fragment-cover.png", params={"model_id": "editable-model", "duration_sec": 88, "video_versions": [
            {"video": "/static/archived.mp4", "cover": "/static/old-cover.png", "createdAt": "2026-10-01T09:00:00Z"}]})
    cover_only = DramaEpisodeFragment(episode_id=episode.id, sort_order=1, cover="/static/not-video.png")
    a.db.add_all([fragment, cover_only])
    short = await short_project(a, project_id=project.id)  # IDs collide across the two project tables.
    short.video_model = "currently-edited-model"
    a.db.add(Shot(project_id=short.id, shot_no=1, video_url="/static/shot.mp4", image_url="/static/shot.png",
        video_prompt="当前修改过的镜头提示词，不能当成原始提交记录"))
    await a.db.flush()
    composition = TaskRun(domain="drama", task_type="episode_compose", queue_type="video", status="succeeded",
        requested_by=a.owner.id, drama_project_id=project.id, episode_id=episode.id,
        result_payload={"kind": "video", "urls": ["/static/composed.mp4"]})
    history = TaskRun(domain="drama", task_type="fragment_video", queue_type="video", status="succeeded",
        requested_by=a.owner.id, drama_project_id=project.id, fragment_id=fragment.id,
        payload={"prompt": "历史实际请求", "model_id": "accepted-model", "duration_sec": 12, "api_key": "private"},
        result_payload={"urls": ["/static/previous-task.mp4"], "preview_url": "/static/history-cover.png"})
    current = TaskRun(domain="drama", task_type="fragment_video", queue_type="video", status="succeeded",
        requested_by=a.owner.id, drama_project_id=project.id, fragment_id=fragment.id,
        payload={"prompt": "当前真实提交提示词", "model_id": "current-model", "duration_sec": 12},
        result_payload={"urls": [fragment.video]})
    a.db.add_all([composition, history, current])
    await a.db.flush()
    a.db.add(ToolRun(user_id=a.owner.id, tool_id="t2v", kind="video", urls=["/static/composed.mp4"],
        params={"platform_task_id": composition.id}))
    # The same URL on an unrelated tool result does not establish project ownership.
    a.db.add(ToolRun(user_id=a.owner.id, tool_id="t2v", kind="video", urls=[fragment.video]))
    await a.db.commit()
    body = (await a.client.get("/api/drama/library-assets", params={"type": "video", "page_size": 50})).json()
    assert body["meta"]["total"] == 7
    assert len({item["key"] for item in body["items"]}) == 7
    linked = [item for item in body["items"] if item["project_kind"] == "drama"]
    assert len(linked) == 4
    assert {item["url"] for item in linked} == {
        "/static/fragment.mp4", "/static/archived.mp4", "/static/composed.mp4", "/static/previous-task.mp4"}
    assert all(item["project_id"] == project.id and item["project_url"] == f"/drama/projects/{project.id}" for item in linked)
    task_item = next(item for item in linked if item["url"] == "/static/previous-task.mp4")
    assert task_item["params"]["media_metadata"]["prompt"] == "历史实际请求"
    assert task_item["params"]["media_metadata"]["model_id"] == "accepted-model"
    current_item = next(item for item in linked if item["url"] == fragment.video)
    assert current_item["source_kind"] == "drama_fragment"
    assert current_item["params"]["media_metadata"]["prompt"] == "当前真实提交提示词"
    assert current_item["params"]["media_metadata"]["model_id"] == "current-model"
    assert current_item["params"]["media_metadata"]["parameters"]["requested_duration_sec"] == 12
    assert next(item for item in linked if item["url"] == "/static/archived.mp4")["params"] is None
    assert "api_key" not in str(body) and "private" not in str(body)
    assert all("_lineage" not in item for item in body["items"])
    shorts = (await a.client.get("/api/drama/library-assets", params={"project_id": short.id, "project_kind": "short_video"})).json()
    assert shorts["meta"]["total"] == 2
    assert all(item["project_kind"] == "short_video" and item["project_url"] == f"/studio/{short.id}/editor" for item in shorts["items"])
    assert all(item["params"] is None for item in shorts["items"])
    drama = (await a.client.get("/api/drama/library-assets", params={"project_id": project.id})).json()
    assert drama["meta"]["total"] == 4
    assert (await a.client.get("/api/drama/library-assets?unassigned=true&type=video")).json()["meta"]["total"] == 1
    assert (await a.client.get("/api/drama/library-assets", params={"q": "完整视频归属", "type": "video"})).json()["meta"]["total"] == 4
    assert (await a.client.get("/api/drama/library-assets?project_kind=unknown")).status_code == 422


@pytest.mark.asyncio
async def test_video_catalog_retains_distinct_targets_and_repeated_generations(drama_api):
    a = drama_api
    project = DramaProject(user_id=a.owner.id, title="复用素材不能误合并")
    a.db.add(project)
    await a.db.flush()
    episode = DramaEpisode(project_id=project.id, name="复用分镜")
    a.db.add(episode)
    await a.db.flush()
    fragments = [DramaEpisodeFragment(episode_id=episode.id, sort_order=i, video="/static/shared.mp4",
        content="可编辑正文不能冒充生成快照", duration_sec=99, params={"model_id": "edited-model"}) for i in range(3)]
    a.db.add_all(fragments)
    await a.db.flush()
    for fragment in fragments[:2]:
        for _ in range(2):
            a.db.add(TaskRun(domain="drama", task_type="fragment_video", queue_type="video", status="succeeded",
                requested_by=a.owner.id, drama_project_id=project.id, fragment_id=fragment.id,
                result_payload={"urls": [fragment.video]}))
    await a.db.commit()
    result = (await a.client.get("/api/drama/library-assets", params={"project_id": project.id, "type": "video"})).json()
    assert result["meta"]["total"] == 5  # Three entities plus one prior attempt for each of the first two.
    assert len([item for item in result["items"] if item["source_kind"] == "drama_fragment"]) == 3
    assert len([item for item in result["items"] if item["source_kind"] == "task"]) == 2
    assert all(item["params"] is None for item in result["items"] if item["source_kind"] == "drama_fragment")


@pytest.mark.asyncio
async def test_video_catalog_checks_project_ownership_workspace_and_current_membership(drama_api):
    from sqlalchemy import select
    from app.models_teams import TeamMember

    a = drama_api
    foreign = DramaProject(user_id=a.outsider.id, title="不能读取的项目")
    a.db.add(foreign)
    await a.db.flush()
    episode = DramaEpisode(project_id=a.project.id, name="团队视频集")
    foreign_episode = DramaEpisode(project_id=foreign.id, name="其他用户分集")
    a.db.add_all([episode, foreign_episode])
    await a.db.flush()
    a.db.add_all([
        DramaEpisodeFragment(episode_id=episode.id, video="/static/team-fragment.mp4"),
        DramaEpisodeFragment(episode_id=foreign_episode.id, video="/static/foreign-fragment.mp4"),
    ])
    foreign_short = await short_project(a, owner=a.outsider)
    team_task = TaskRun(domain="drama", task_type="episode_compose", queue_type="video", status="succeeded",
        requested_by=a.owner.id, team_id=a.team_id, drama_project_id=a.project.id,
        result_payload={"urls": ["/static/team-compose.mp4"]})
    a.db.add_all([team_task, TaskRun(domain="drama", task_type="fragment_video", queue_type="video", status="succeeded",
        requested_by=a.owner.id, drama_project_id=foreign.id, result_payload={"urls": ["/static/stale-foreign.mp4"]})])
    await a.db.flush()
    a.db.add(ToolRun(user_id=a.owner.id, tool_id="t2v", kind="video", urls=["/static/team-compose.mp4"],
        params={"platform_task_id": team_task.id}))
    await a.db.commit()
    assert (await a.client.get("/api/drama/library-assets?type=video")).json()["items"] == []
    assert (await a.client.get("/api/drama/library-assets", params={"project_id": foreign_short.id, "project_kind": "short_video"})).status_code == 404
    a.auth["user"] = a.member
    result = (await a.client.get("/api/drama/library-assets?type=video", headers={"X-Team-Id": str(a.team_id)})).json()
    assert {item["url"] for item in result["items"]} == {"/static/team-fragment.mp4", "/static/team-compose.mp4"}
    assert (await a.client.get("/api/drama/library-assets?unassigned=true&type=video", headers={"X-Team-Id": str(a.team_id)})).json()["items"] == []
    member = await a.db.scalar(select(TeamMember).where(TeamMember.team_id == a.team_id, TeamMember.user_id == a.member.id))
    member.active = False
    await a.db.commit()
    assert (await a.client.get("/api/drama/library-assets?type=video", headers={"X-Team-Id": str(a.team_id)})).status_code == 404


@pytest.mark.asyncio
async def test_catalog_uses_formal_step_outputs_and_preserves_failed_partial_delivery(drama_api):
    a = drama_api
    task = TaskRun(domain="studio", task_type="tool_video", queue_type="video", status="failed", requested_by=a.owner.id,
        payload={"input_url": "/static/input.mp4"}, result_payload={"preview_url": "/static/preview.mp4"})
    preview_only = TaskRun(domain="studio", task_type="tool_video", queue_type="video", status="succeeded", requested_by=a.owner.id,
        payload={"video_url": "/static/reference.mp4"}, result_payload={"preview_url": "/static/poster.mp4"})
    a.db.add_all([task, preview_only])
    await a.db.flush()
    a.db.add(TaskStep(task_id=task.id, step_key="video", output_payload={"video_url": "/static/delivered.mp4"}))
    await a.db.commit()
    result = (await a.client.get("/api/drama/library-assets?type=video&unassigned=true")).json()
    assert [item["url"] for item in result["items"]] == ["/static/delivered.mp4"]
