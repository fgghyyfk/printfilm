"""保存分镜不能覆盖服务端写入的成片和运行态；后台回填不能覆盖用户刚保存的内容。"""
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.drama.episodes import save_fragments
from app.models import User
from app.models_drama import DramaEpisodeFragment
from app.schemas_drama import DramaSaveFragmentsRequest
from app.services.drama import generation
from tests.drama_fragment_helpers import make_episode
from tests.tts_database import db_session  # noqa: F401 — 真实提交，独立连接才能看见

SERVER_PARAMS = {
    "video_versions": [{"id": "v2", "url": "/static/v2.mp4"}],
    "lastFrameUrl": "/static/last.png",
    "generation": {"status": "done"},
    "resolution": "720p",
}


async def _generated_fragment(db):
    """分镜已生成成片，且带用户设置。"""
    user, project, episode, rows = await make_episode(db)
    frag = rows[0]
    frag.video = "/static/v2.mp4"
    frag.cover = "/static/cover.png"
    frag.params = {**SERVER_PARAMS, "user_setting": "old", "removed_setting": "x"}
    await db.commit()
    return user, project, episode, rows


def _item(row, **changes):
    """模拟旧页面保存：媒体是打开页面时的旧值，params 里没有新成片。"""
    return {
        "id": row.id, "sort_order": row.sort_order, "content": "用户修改", "cover": "", "video": "",
        "duration_sec": 8, "params": {"user_setting": "new"}, "asset_ids": [], **changes,
    }


async def _save(db, user, episode, items):
    """走真实保存接口；带上当前分集版本号（合并后保存必须带 revision）。"""
    await db.refresh(episode, attribute_names=["revision"])
    body = DramaSaveFragmentsRequest(revision=episode.revision or 0, fragments=items)
    return await save_fragments(episode.id, body, db=db, user=user)


async def test_stale_page_save_keeps_generated_media(db_session):
    """批量生成期间保存：成片、封面和运行态都不能被旧页面抹掉。"""
    user, _, episode, rows = await _generated_fragment(db_session)
    await _save(db_session, user, episode, [_item(row) for row in rows])
    await db_session.refresh(rows[0])
    frag = rows[0]
    assert frag.video == "/static/v2.mp4" and frag.cover == "/static/cover.png"
    for key, value in SERVER_PARAMS.items():
        assert frag.params[key] == value


async def test_save_still_applies_user_edits(db_session):
    """用户内容和设置照常写入，包括删除设置项。"""
    user, _, episode, rows = await _generated_fragment(db_session)
    await _save(db_session, user, episode, [_item(row) for row in rows])
    await db_session.refresh(rows[0])
    assert "用户修改" in rows[0].content
    assert rows[0].params["user_setting"] == "new"
    assert "removed_setting" not in rows[0].params


async def test_client_cannot_inject_server_fields(db_session):
    """客户端传来的成片地址和运行态不能写入已有分镜。"""
    user, _, episode, rows = await _generated_fragment(db_session)
    items = [_item(rows[0], video="https://evil.test/x.mp4", params={"generation": {"status": "failed"}}),
             _item(rows[1])]
    await _save(db_session, user, episode, items)
    await db_session.refresh(rows[0])
    assert rows[0].video == "/static/v2.mp4"
    assert rows[0].params["generation"] == {"status": "done"}


async def test_new_fragment_is_still_created(db_session):
    """无 id 的新增分镜照常创建。"""
    user, _, episode, rows = await _generated_fragment(db_session)
    items = [_item(row) for row in rows] + [_item(rows[0], id=None, sort_order=9, content="新增分镜")]
    saved = await _save(db_session, user, episode, items)
    assert len(saved.fragments) == 3


async def test_worker_writeback_rereads_user_edit(db_session, monkeypatch):
    """下载成片期间用户保存了设置：回填要基于最新内容，不能用下载前的旧快照覆盖。"""
    user, project, episode, rows = await _generated_fragment(db_session)
    worker_frag = rows[0]
    assert worker_frag.params["user_setting"] == "old"
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with sessions() as other:
        row = await other.get(DramaEpisodeFragment, worker_frag.id)
        row.params = {**row.params, "user_setting": "edited-during-download"}
        await other.commit()
    monkeypatch.setattr(generation, "_record_fragment_seedance_usage", _noop, raising=False)
    await generation.apply_fragment_video_assets(
        db_session, await db_session.get(User, user.id), project, worker_frag, local_video="/static/v3.mp4",
    )
    assert worker_frag.video.endswith("/static/v3.mp4")
    assert worker_frag.params["user_setting"] == "edited-during-download"
    assert worker_frag.params["generation"]["status"] == "done"


async def _noop(*args, **kwargs):
    """测试不关心计费。"""
    return None
