"""本地整集合成的任务快照、零费用与不可变输出。"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.drama.episodes import compose_episode
from app.models import UsageEvent
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from app.models_tasks import TaskRun
from app.schemas_drama import DramaComposeEpisodeRequest
from app.services.tasks import executor
from app.services.tasks.service import get_task_for_runtime
from tests.conftest import make_task, make_user


async def compose_context(db):
    """创建含两条可用视频的分集，插入顺序与播放顺序相反。"""
    user = await make_user(db, balance_fen=0)
    project = DramaProject(user_id=user.id, title="合成快照", params={"aspect_ratio": "16:9"})
    db.add(project)
    await db.flush()
    episode = DramaEpisode(project_id=project.id, name="第一集", params={"resolution": "720p"})
    db.add(episode)
    await db.flush()
    fragments = [DramaEpisodeFragment(episode_id=episode.id, sort_order=order, video=f"/static/{name}.mp4")
                 for order, name in [(1, "a"), (0, "b")]]
    db.add_all(fragments)
    await db.commit()
    return user, project, episode, fragments


async def test_compose_is_free_and_outputs_are_immutable(db_session, tmp_path, monkeypatch):
    """余额零且有付费排队承诺仍免费；执行只用入队快照，重执行不覆旧文件。"""
    from app.services import storage
    from app.services.drama import episode_compose as service
    from app.services.tasks.handlers import get_task_handler

    user, project, episode, fragments = await compose_context(db_session)
    pending = await make_task(db_session, user)
    pending.billing_estimate_fen = 99
    await db_session.commit()
    rates = AsyncMock(side_effect=AssertionError("免费合成不能请求价目"))
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", rates)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path / "generated")
    for name in ("a", "b"):
        (tmp_path / f"{name}.mp4").write_bytes(name.encode())
    sizes = []

    def normalize(src, dest, width, height):
        """受控替代编码器，保留输入顺序及目标规格证据。"""
        sizes.append((width, height))
        dest.write_bytes(src.read_bytes())

    def concat(paths, output):
        """受控替代拼接器，输出字节可验证固定的镜头顺序。"""
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"".join(path.read_bytes() for path in paths))

    monkeypatch.setattr(service, "_normalize_clip", normalize)
    monkeypatch.setattr(service, "_concat_normalized", concat)
    monkeypatch.setattr(storage, "publish_local", lambda path, **_kw: storage.rel_static_url(path))
    queued = await compose_episode(episode.id, DramaComposeEpisodeRequest(), db=db_session, user=user)
    assert "task_id" in queued and not sizes, "HTTP只能入队，不应内联编码"
    task_id = queued["task_id"]
    task = await get_task_for_runtime(db_session, task_id)
    assert task.cancelable is False and task.billing_estimate_fen == 0
    fragments[0].video = "/static/new.mp4"
    episode.params = {"aspect_ratio": "1:1", "resolution": "1080p"}
    await db_session.commit()

    @asynccontextmanager
    async def same_session():
        """执行器仍走真实冻结、结果与结算路径。"""
        yield db_session

    monkeypatch.setattr(executor, "AsyncSessionLocal", same_session)
    await executor.execute_task_run(task_id)
    done = await get_task_for_runtime(db_session, task_id)
    assert done.status == "succeeded"
    assert (done.billing_charged_fen, done.billing_estimate_fen) == (0, 0)
    first_url = done.result_payload["video_url"]
    first_path = storage.local_path_from_url(first_url)
    assert first_path.read_bytes() == b"ba" and sizes == [(1280, 720), (1280, 720)]
    assert first_path.is_relative_to(tmp_path / "generated" / "drama" / f"p{project.id}")
    rerun = await get_task_handler("drama", "episode_compose").executor(done)
    second_path = storage.local_path_from_url(rerun["video_url"])
    assert first_path != second_path and first_path.read_bytes() == second_path.read_bytes() == b"ba"
    assert await db_session.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.task_run_id == task_id)) == 0
    rates.assert_not_awaited()


@pytest.mark.parametrize("selection", ["empty", "foreign"])
async def test_compose_rejects_empty_or_foreign_selection(db_session, monkeypatch, selection):
    """显式空集不是全选，分集外的镜号不能进入快照。"""
    from app.services.drama import episode_compose as service

    user, project, episode, _fragments = await compose_context(db_session)
    other = DramaEpisode(project_id=project.id, name="另一集")
    db_session.add(other)
    await db_session.flush()
    foreign = DramaEpisodeFragment(episode_id=other.id, video="/static/other.mp4")
    db_session.add(foreign)
    await db_session.commit()
    ids = [] if selection == "empty" else [foreign.id]
    monkeypatch.setattr(service, "compose_episode_video", AsyncMock(return_value="/static/ignored.mp4"))
    with pytest.raises(HTTPException) as error:
        await compose_episode(episode.id, DramaComposeEpisodeRequest(fragment_ids=ids), db=db_session, user=user)
    assert error.value.status_code == 400
    assert error.value.headers["X-Error-Code"] == "invalid_input"
    if selection == "foreign":
        assert "重新选择" in error.value.detail
    assert await db_session.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_type == "episode_compose")) == 0


async def test_compose_failure_and_task_owner_are_preserved(db_session, tmp_path, monkeypatch):
    """FFmpeg失败由原任务收敛，另一个用户不能查看其合成任务。"""
    from app.services.drama import episode_compose as service
    from app.services.tasks.service import get_task_for_user

    user, _project, episode, _fragments = await compose_context(db_session)
    queued = await compose_episode(episode.id, db=db_session, user=user)
    other = await make_user(db_session)
    await db_session.commit()
    with pytest.raises(LookupError):
        await get_task_for_user(db_session, other, queued["task_id"])
    monkeypatch.setattr("app.services.storage.GENERATED_ROOT", tmp_path)
    monkeypatch.setattr(service, "_ensure_clip_local", AsyncMock(return_value=tmp_path / "clip.mp4"))

    def fail(*_args):
        """只在本地模拟编码失败，不运行真实FFmpeg或访问媒体。"""
        raise RuntimeError("FFmpeg failed: /private/secret")

    monkeypatch.setattr(service, "_normalize_clip", fail)

    @asynccontextmanager
    async def same_session():
        """以真实执行器验证失败收尾。"""
        yield db_session

    monkeypatch.setattr(executor, "AsyncSessionLocal", same_session)
    await executor.execute_task_run(queued["task_id"])
    done = await get_task_for_runtime(db_session, queued["task_id"])
    assert done.status == "failed" and "合成" in done.error_message
    assert "/private/secret" not in done.error_message
    assert done.billing_charged_fen == 0
