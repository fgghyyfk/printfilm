"""画布布局保存不得覆盖资产库中的最新媒体。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.drama import canvas
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.schemas_drama import DramaCanvasSaveRequest


@pytest.fixture
def canvas_context(monkeypatch):
    """使用真实 ORM 对象，隔离项目读取和提交以避免连接业务库。"""
    # 项目保留非画布内容，数据库仅记录本次新增与提交。
    project = DramaProject(id=7, user_id=11, content={"summary": "保留剧本"}, assets=[])
    user = User(id=11)
    db = MagicMock(spec=AsyncSession)
    db.commit = AsyncMock()

    async def owned_project(actual_db, project_id, actual_user, *, with_assets):
        """确认保存仍按当前用户加载项目及资产。"""
        assert actual_db is db
        assert project_id == project.id
        assert actual_user is user
        assert with_assets is True
        return project

    monkeypatch.setattr(canvas, "get_owned_drama_project", owned_project)
    return project, user, db


@pytest.mark.parametrize("match_by", ["asset_id", "canvas_node_id"])
@pytest.mark.parametrize("media_type", ["image", "video", "audio"])
async def test_save_canvas_preserves_existing_asset_media(canvas_context, match_by, media_type):
    """旧画布快照只能更新名称、布局，不能恢复旧媒体或类型。"""
    # 模拟画布打开后，资产库已生成新媒体及封面。
    project, user, db = canvas_context
    suffix = {"image": "png", "video": "mp4", "audio": "mp3"}[media_type]
    latest_url = f"/static/new.{suffix}"
    latest_cover = None if media_type == "audio" else "/static/new-cover.png"
    asset = DramaAsset(
        id=23,
        project_id=project.id,
        type="character" if match_by == "asset_id" else "none",
        asset_type=media_type,
        name="旧名称",
        url=latest_url,
        cover=latest_cover,
        params={"canvas_node_id": "node-1", "generation": {"status": "done"}},
    )
    project.assets.append(asset)
    original_type = asset.type
    node = {
        "id": "node-1",
        "position": {"x": 100, "y": 200},
        "data": {
            "label": "新名称",
            "kind": "video",
            "asset_type": "video",
            "mediaUrl": "https://api.example.test/static/old.mp4",
            "cover": "https://api.example.test/static/old-cover.png",
        },
    }
    if match_by == "asset_id":
        node["data"]["assetId"] = str(asset.id)
    body = DramaCanvasSaveRequest(project_id=project.id, nodes=[node], edges=[{"id": "edge-1"}])

    result = await canvas.save_canvas(body, db, user)

    assert asset.url == latest_url
    assert asset.cover == latest_cover
    assert asset.type == original_type
    assert asset.asset_type == media_type
    assert asset.name == "新名称"
    assert asset.params == {
        "canvas_node_id": "node-1",
        "generation": {"status": "done"},
        "canvas": node,
    }
    assert project.content == {"summary": "保留剧本", "canvas_nodes": [node], "canvas_edges": body.edges}
    assert result == {"ok": True, "nodes": 1, "edges": 1}
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("kind, suffix", [("video", "mp4"), ("audio", "mp3"), ("voice", "mp3")])
@pytest.mark.parametrize("cover_kind", ["missing", "image", "media"])
async def test_save_canvas_new_timed_media_keeps_url_without_media_cover(
    canvas_context, kind, suffix, cover_kind
):
    """新建视频和音频保留原媒体，只将明确的图片写作封面。"""
    # 媒体 URL 包含主机前缀和查询参数，应原样存储。
    project, user, db = canvas_context
    media_url = f"https://cdn.example.test/new.{suffix}?token=test"
    image_cover = "https://cdn.example.test/cover.JPG?size=small#preview"
    data = {"kind": kind, "asset_type": kind, "label": "新媒体", "mediaUrl": media_url}
    if cover_kind != "missing":
        data["cover"] = image_cover if cover_kind == "image" else media_url
    node = {"id": "new-node", "data": data, "position": {"x": 1, "y": 2}}

    await canvas.save_canvas(DramaCanvasSaveRequest(project_id=project.id, nodes=[node]), db, user)

    asset = db.add.call_args.args[0]
    assert isinstance(asset, DramaAsset)
    assert asset.url == media_url
    assert asset.cover == (image_cover if cover_kind == "image" else None)
    assert asset.type == kind
    assert asset.asset_type == kind
    assert asset.params == {"canvas_node_id": "new-node", "canvas": node}
    db.commit.assert_awaited_once()


async def test_save_canvas_new_image_keeps_media(canvas_context):
    """新建图片节点仍保存原 URL 与图片封面。"""
    project, user, db = canvas_context
    image_url = "https://cdn.example.test/new.png"
    node = {"id": "new-image", "data": {"kind": "image", "mediaUrl": image_url}}

    await canvas.save_canvas(DramaCanvasSaveRequest(project_id=project.id, nodes=[node]), db, user)

    asset = db.add.call_args.args[0]
    assert asset.url == image_url
    assert asset.cover == image_url
    assert asset.asset_type == "image"
