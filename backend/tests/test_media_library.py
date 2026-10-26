"""Global catalog isolation, delivery identity and media classification."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models import ToolRun
from app.models_drama import DramaAsset, DramaProject
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
    assert items[0].params["media_metadata"]["parameters"]["duration_sec"] == 9
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
    for invalid in ["type=text", "page=0", "page_size=100", "project_id=0"]:
        assert (await a.client.get(f"/api/drama/library-assets?{invalid}")).status_code == 422
