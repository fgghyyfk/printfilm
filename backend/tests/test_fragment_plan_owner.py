"""强制重新分镜时只有最新任务能落库；等待模型期间的手改要被保护（真实双连接）。"""
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.drama.episodes import plan_episode_fragments, update_episode
from app.models import User
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.schemas_drama import DramaEpisodeUpdate, DramaPlanFragmentsRequest
from app.services.billing import settlement
from app.services.drama import jobs
from tests.conftest import make_user
from tests.tts_database import db_session  # noqa: F401 — 真实提交，独立连接才能看见

OLD_CONTENT = "用户已有的分镜"
NEW_DRAFT = {"content": "模型写的新分镜", "duration_sec": 8, "asset_ids": []}


@pytest_asyncio.fixture
async def planning(db_session, monkeypatch):
    """提交一集正在规划的分集，退出时清理。"""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "AsyncSessionLocal", sessions)
    user = await make_user(db_session)
    project = DramaProject(user_id=user.id, title="t")
    db_session.add(project)
    await db_session.flush()
    db_session.add(DramaScript(project_id=project.id, episode_content=[{"episodeNumber": 1, "body": "正文内容"}]))
    episode = DramaEpisode(project_id=project.id, name="第1集",
                           params={"episodeNumber": 1, "fragment_plan_status": "generating"})
    db_session.add(episode)
    await db_session.flush()
    fragment = DramaEpisodeFragment(episode_id=episode.id, sort_order=0, content=OLD_CONTENT, params={})
    db_session.add(fragment)
    await db_session.commit()
    ids = user.id, project.id, episode.id, fragment.id
    try:
        yield sessions, user, ids
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(TaskRun).where(TaskRun.requested_by == ids[0]))
            await cleanup.execute(delete(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == ids[2]))
            await cleanup.execute(delete(DramaEpisode).where(DramaEpisode.id == ids[2]))
            await cleanup.execute(delete(DramaScript).where(DramaScript.project_id == ids[1]))
            await cleanup.commit()


def _llm(side_effect=None):
    """替换模型调用；可在“等待模型”期间执行并发操作。"""
    async def plan(*args, **kwargs):
        if side_effect:
            await side_effect()
        return [dict(NEW_DRAFT)]
    return patch("app.services.drama.fragment_plan.plan_fragments_with_llm", plan)


async def _state(sessions, episode_id):
    """读取已提交的分镜内容与规划状态。"""
    async with sessions() as db:
        params = (await db.get(DramaEpisode, episode_id)).params
        contents = (await db.scalars(select(DramaEpisodeFragment.content)
                                     .where(DramaEpisodeFragment.episode_id == episode_id))).all()
    return params, list(contents)


async def _set_owner(sessions, episode_id, owner):
    """模拟新规划任务接管分集。"""
    async with sessions() as db:
        episode = await db.get(DramaEpisode, episode_id)
        episode.params = {**episode.params, "fragment_plan_task_id": owner}
        await db.commit()


async def test_superseded_task_discards_result(planning):
    """旧任务完成时已有新任务接管：不能覆盖分镜，也不能改状态。"""
    sessions, _, (_, _, eid, _) = planning
    await _set_owner(sessions, eid, 200)
    with _llm():
        result = await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=True, task_id=100)
    params, contents = await _state(sessions, eid)
    assert result.get("superseded") is True
    assert contents == [OLD_CONTENT]
    assert params["fragment_plan_status"] == "generating"


async def test_owner_changed_during_llm_wait_discards_result(planning):
    """等待模型期间被新任务接管（真实时序）。"""
    sessions, _, (_, _, eid, _) = planning
    await _set_owner(sessions, eid, 100)

    async def takeover():
        await _set_owner(sessions, eid, 200)
    with _llm(takeover):
        await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=True, task_id=100)
    _, contents = await _state(sessions, eid)
    assert contents == [OLD_CONTENT]


async def test_current_owner_applies_result(planning):
    """自己是当前任务：正常替换并完成。"""
    sessions, _, (_, _, eid, _) = planning
    await _set_owner(sessions, eid, 100)
    with _llm():
        await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=True, task_id=100)
    params, contents = await _state(sessions, eid)
    assert params["fragment_plan_status"] == "completed"
    assert contents == [NEW_DRAFT["content"]]


async def test_legacy_task_without_owner_still_applies(planning):
    """发布前已在排队的任务没有归属记录，不能被卡住。"""
    sessions, _, (_, _, eid, _) = planning
    with _llm():
        await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=True, task_id=100)
    params, contents = await _state(sessions, eid)
    assert params["fragment_plan_status"] == "completed" and contents == [NEW_DRAFT["content"]]


async def test_superseded_failure_does_not_mark_failed(planning):
    """旧任务模型失败时，不能把新任务的 generating 改成失败。"""
    sessions, _, (_, _, eid, _) = planning
    await _set_owner(sessions, eid, 200)
    with patch("app.services.drama.fragment_plan.plan_fragments_with_llm", AsyncMock(side_effect=RuntimeError("模型超时"))):
        await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=True, fallback_rules=False, task_id=100)
    params, _ = await _state(sessions, eid)
    assert params["fragment_plan_status"] == "generating"


async def test_user_edit_during_llm_wait_is_protected(planning):
    """非强制规划等待模型期间用户手改了分镜：要按最新内容保护，不能用旧快照删掉。"""
    sessions, _, (_, _, eid, fid) = planning

    async def edit():
        async with sessions() as db:
            fragment = await db.get(DramaEpisodeFragment, fid)
            fragment.content = "等待期间的手改"
            fragment.params = {"user_edited": True}
            await db.commit()
    with _llm(edit):
        await jobs.run_episode_fragment_plan_job(eid, expected_revision=0, force=False, task_id=None)
    _, contents = await _state(sessions, eid)
    assert "等待期间的手改" in contents


async def test_api_records_owner_in_same_commit(planning):
    """入队和归属同事务提交，不会出现任务已建却没有归属的窗口。"""
    sessions, _, (uid, _, eid, _) = planning
    async with sessions() as db:
        user = await db.get(User, uid)
        with patch.object(settlement, "ensure_balance_for_task", AsyncMock(return_value=0)):
            await plan_episode_fragments(eid, DramaPlanFragmentsRequest(force=True), db=db, user=user)
    params, _ = await _state(sessions, eid)
    async with sessions() as db:
        tasks = (await db.scalars(select(TaskRun).where(TaskRun.episode_id == eid))).all()
    assert len(tasks) == 1 and params["fragment_plan_task_id"] == tasks[0].id


async def test_dispatch_records_owner(planning):
    """自动入队入口同样写归属。"""
    sessions, _, (uid, _, eid, _) = planning
    async with sessions() as db:
        user = await db.get(User, uid)
        episode = await db.get(DramaEpisode, eid)
        with patch.object(settlement, "ensure_balance_for_task", AsyncMock(return_value=0)):
            task_id = await jobs.dispatch_episode_fragment_plan(db, user, episode, force=True)
    params, _ = await _state(sessions, eid)
    assert params["fragment_plan_task_id"] == task_id


async def test_stale_page_cannot_change_owner(planning):
    """旧页面保存分集参数时带回旧归属：不能改掉当前任务。"""
    sessions, _, (uid, _, eid, _) = planning
    await _set_owner(sessions, eid, 200)
    async with sessions() as db:
        user = await db.get(User, uid)
        await update_episode(eid, DramaEpisodeUpdate(params={"fragment_plan_task_id": 100, "note": "x"}), db=db, user=user)
    params, _ = await _state(sessions, eid)
    assert params["fragment_plan_task_id"] == 200 and params["note"] == "x"
