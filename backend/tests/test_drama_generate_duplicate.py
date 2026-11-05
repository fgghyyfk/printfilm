"""同一分镜并发提交生成时只能入队一次（真实 PostgreSQL 双连接）。"""
import asyncio

import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.drama import episodes
from app.models import User
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from app.models_tasks import TaskRun
from app.schemas_drama import DramaGenerateRequest
from tests.drama_fragment_helpers import make_episode


@pytest_asyncio.fixture
async def committed_episode(db_session):
    """向测试库提交两镜分集，退出时删除本用例写入的行。"""
    sessions = async_sessionmaker(db_session.bind.engine, expire_on_commit=False)
    async with sessions() as db:
        user, project, episode, rows = await make_episode(db)
        await db.commit()
        ids = user.id, project.id, episode.id, [row.id for row in rows]
    try:
        yield sessions, ids
    finally:
        uid, pid, eid, _ = ids
        async with sessions() as db:
            await db.execute(delete(TaskRun).where(TaskRun.requested_by == uid))
            await db.execute(delete(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == eid))
            await db.execute(delete(DramaEpisode).where(DramaEpisode.id == eid))
            await db.execute(delete(DramaProject).where(DramaProject.id == pid))
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()


def _stale_read_barrier(monkeypatch):
    """让两个请求都读到空闲状态后再继续，稳定复现检查与入队之间的竞态窗口。"""
    barrier = asyncio.Barrier(2)
    real_load = episodes.load_episode_fragments

    async def load(db, episode_id):
        """仅首读等待对方，锁内重读不再等待。"""
        rows = await real_load(db, episode_id)
        await asyncio.wait_for(barrier.wait(), 5)
        return rows

    monkeypatch.setattr(episodes, "load_episode_fragments", load)


async def _request(sessions, uid, eid, fragment_ids):
    """用独立连接发起一次真实的生成请求。"""
    async with sessions() as db:
        user = await db.get(User, uid)
        return await episodes.generate_episode(
            eid, DramaGenerateRequest(fragment_ids=fragment_ids), db=db, user=user,
        )


async def _fragment_tasks(sessions, eid):
    """读取本集已创建的分镜视频任务。"""
    async with sessions() as db:
        return list((await db.scalars(select(TaskRun).where(
            TaskRun.episode_id == eid, TaskRun.task_type == "fragment_video",
        ))).all())


async def test_concurrent_generate_same_fragment_enqueues_once(committed_episode, monkeypatch):
    """双标签页同时点生成：一个入队，另一个 409，不会提交两次付费任务。"""
    sessions, (uid, _, eid, fragment_ids) = committed_episode
    _stale_read_barrier(monkeypatch)
    results = await asyncio.gather(
        _request(sessions, uid, eid, [fragment_ids[0]]),
        _request(sessions, uid, eid, [fragment_ids[0]]),
        return_exceptions=True,
    )
    rejected = [r for r in results if isinstance(r, HTTPException)]
    assert len(await _fragment_tasks(sessions, eid)) == 1
    assert len(rejected) == 1 and rejected[0].status_code == 409


async def test_concurrent_batches_only_enqueue_each_fragment_once(committed_episode, monkeypatch):
    """两个批次部分重叠：重叠镜头只入队一次，未重叠的镜头仍正常入队。"""
    sessions, (uid, _, eid, fragment_ids) = committed_episode
    _stale_read_barrier(monkeypatch)
    results = await asyncio.gather(
        _request(sessions, uid, eid, fragment_ids),
        _request(sessions, uid, eid, [fragment_ids[0]]),
        return_exceptions=True,
    )
    tasks = await _fragment_tasks(sessions, eid)
    assert sorted(task.fragment_id for task in tasks) == sorted(fragment_ids), results
