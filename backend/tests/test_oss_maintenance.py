"""OSS补传不占维护循环，线程结束前不解URL互斥；JSON新读合并。"""
import asyncio
import threading
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models_drama import DramaAsset, DramaProject
from app.services import oss_queue
from app.services.tasks import scheduler
from tests.conftest import make_user
from tests.tts_database import db_session


async def test_slow_backfill_is_tracked_without_blocking_maintenance_or_early_unlock(monkeypatch, tmp_path):
    url = "/static/generated/p99/final.mp4"
    local = tmp_path / "final.mp4"
    local.write_bytes(b"keep local")
    started, release = threading.Event(), threading.Event()
    unlocked = []
    monkeypatch.setattr(oss_queue.oss_svc, "oss_enabled", lambda: True)
    monkeypatch.setattr(oss_queue, "_upload_tasks", {})
    monkeypatch.setattr(oss_queue, "_backfill_task", None)
    monkeypatch.setattr(oss_queue, "_next_backfill_at", 0)
    monkeypatch.setattr(oss_queue, "collect_pending_local_media_urls", AsyncMock(return_value=[url]))
    monkeypatch.setattr(oss_queue, "_claim_upload", lambda _: "owned")
    monkeypatch.setattr(oss_queue, "clear_enqueue_marker", lambda *args: unlocked.append(release.is_set()))
    monkeypatch.setattr(oss_queue, "backfill_media_url", AsyncMock(return_value=1))
    def upload(_):
        started.set()
        assert release.wait(5)
        return "https://cdn.test/final.mp4"
    monkeypatch.setattr(oss_queue, "upload_local_url_sync", upload)
    recover = AsyncMock()
    monkeypatch.setattr(scheduler, "recover_orphaned_tasks", recover)
    await asyncio.wait_for(scheduler._run_maintenance_operation("oss_backfill"), .5)
    assert await asyncio.to_thread(started.wait, 2)
    job = oss_queue._backfill_task
    await scheduler._run_maintenance_operation("orphan_recovery")
    recover.assert_awaited_once()
    assert not oss_queue.start_pending_media_backfill_if_due()
    assert not oss_queue.enqueue_oss_upload(url)
    job.cancel()
    await asyncio.sleep(0)
    assert not job.done() and not unlocked
    release.set()
    await asyncio.gather(job, return_exceptions=True)
    assert unlocked == [True]
    assert local.read_bytes() == b"keep local"
    monkeypatch.setattr(oss_queue.oss_svc, "oss_enabled", lambda: False)
    scan = AsyncMock(side_effect=AssertionError("OSS关闭不扫库"))
    monkeypatch.setattr(oss_queue, "collect_pending_local_media_urls", scan)
    assert not (await oss_queue.backfill_pending_local_media())["ok"]
    scan.assert_not_awaited()


async def test_backfill_nested_params_keeps_concurrent_history_and_image_revision(db_session, monkeypatch):
    user = await make_user(db_session)
    project = DramaProject(user_id=user.id, title="OSS")
    db_session.add(project)
    await db_session.flush()
    old, new = "/static/generated/p9/voice.mp3", "https://cdn.test/voice.mp3"
    asset = DramaAsset(project_id=project.id, name="音色", image_revision="media-original",
                       params={"canvas": {"voiceAudio": old}, "image_versions": []})
    db_session.add(asset)
    await db_session.commit()
    aid = asset.id
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    reached, edited = asyncio.Event(), asyncio.Event()
    execute = db_session.execute
    async def gated(statement, *args, **kwargs):
        if getattr(statement, "is_select", False):
            reached.set()
            await asyncio.wait_for(edited.wait(), 5)
        return await execute(statement, *args, **kwargs)
    monkeypatch.setattr(db_session, "execute", gated)
    @asynccontextmanager
    async def session():
        yield db_session
    monkeypatch.setattr("app.database.AsyncSessionLocal", session)
    async def edit():
        await asyncio.wait_for(reached.wait(), 5)
        async with sessions() as db:
            current = await db.get(DramaAsset, aid)
            params = deepcopy(current.params)
            params["image_versions"].append({"url": "/static/history.png"})
            params["live_setting"] = "keep"
            current.params = params
            await db.commit()
        edited.set()
    await asyncio.wait_for(asyncio.gather(oss_queue.backfill_media_url(old, new), edit()), 10)
    async with sessions() as db:
        row = await db.get(DramaAsset, aid)
        assert row.params == {"canvas": {"voiceAudio": new}, "image_versions": [{"url": "/static/history.png"}], "live_setting": "keep"}
        assert row.image_revision == "media-original"
