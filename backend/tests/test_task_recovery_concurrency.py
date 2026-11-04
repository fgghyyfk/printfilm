"""真实独立 PostgreSQL 连接的孤儿扫描竞争及旧批次防重消费。"""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import UsageEvent, User, WalletLedger
from app.models_tasks import TaskEvent, TaskRun
from app.services.tasks import executor, recovery, scheduler
from tests.conftest import make_task, make_user


@pytest_asyncio.fixture
async def recovery_connections(db_session, monkeypatch):
    """只向测试库提交竞态数据，退出时删除本用例的账本及用户。"""
    sessions = async_sessionmaker(db_session.bind.engine, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    monkeypatch.setattr("app.services.billing.alerts.process_billing_alerts_after_charge", AsyncMock())
    async with sessions() as db:
        user = await make_user(db, balance_fen=900, frozen_fen=100)
        uid = user.id
        await db.commit()
    try:
        yield sessions, uid
    finally:
        async with sessions() as db:
            await db.execute(delete(UsageEvent).where(UsageEvent.user_id == uid))
            await db.execute(delete(WalletLedger).where(WalletLedger.user_id == uid))
            await db.execute(delete(TaskRun).where(TaskRun.requested_by == uid))
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()


async def stale_task(sessions, uid, *, count=0, frozen=False):
    """提交独立连接可见的过期任务与可选真实成本。"""
    async with sessions() as db:
        user = await db.get(User, uid)
        task = await make_task(db, user, domain="kepu", task_type="project_compose_only", status="running",
                               billing_status="frozen" if frozen else "none")
        task.recovery_count, task.billing_estimate_fen = count, 100 if frozen else 0
        if frozen:
            db.add(UsageEvent(user_id=uid, task_run_id=task.id, billing_key="llm_chat", charge_fen=30))
        await db.flush()
        await db.execute(update(TaskRun).where(TaskRun.id == task.id).values(
            updated_at=datetime.now(UTC) - timedelta(hours=1)))
        await db.commit()
        return task.id


@pytest.mark.parametrize("count", [0, 2])
async def test_two_scans_same_stale_row_only_recover_once(recovery_connections, monkeypatch, count):
    """两个扫描先同时读到同一候选，再竞争行锁，只记一次计数/事件/结算。"""
    sessions, uid = recovery_connections
    tid = await stale_task(sessions, uid, count=count, frozen=True)
    original, arrived, ready = recovery.recover_interrupted_task, 0, asyncio.Event()

    async def barrier(*args, **kwargs):
        """候选扫描完成后再一起进入真正的锁重读。"""
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return await original(*args, **kwargs)
    monkeypatch.setattr(recovery, "recover_interrupted_task", barrier)
    changed = await asyncio.gather(scheduler.recover_orphaned_tasks(), scheduler.recover_orphaned_tasks())
    assert sum(changed) == 1
    async with sessions() as db:
        task, user = await db.get(TaskRun, tid), await db.get(User, uid)
        assert task.recovery_count == count + 1
        assert task.status == ("failed" if count == 2 else "pending")
        events = list(await db.scalars(select(TaskEvent).where(TaskEvent.task_id == tid)))
        assert len(events) == 1
        refunds = list(await db.scalars(select(WalletLedger).where(
            WalletLedger.ref_id == str(tid), WalletLedger.kind == "unfreeze")))
        assert len(refunds) == (1 if count == 2 else 0)
        assert user.balance_fen == (970 if count == 2 else 900)


async def test_failure_commit_does_not_leave_old_bulk_row_to_reconsume(recovery_connections, monkeypatch):
    """首行到限结算提交后，另一维护者回收第二行；原列表不得再次消费它。"""
    sessions, uid = recovery_connections
    first = await stale_task(sessions, uid, count=2, frozen=True)
    second = await stale_task(sessions, uid)
    failed, release = asyncio.Event(), asyncio.Event()
    original = executor._fail_task

    async def pause_after_failure(db, task, exc):
        """在首条真实失败已提交、第二条尚未锁读时停住扫描。"""
        await original(db, task, exc)
        if task.id == first:
            failed.set()
            await asyncio.wait_for(release.wait(), 5)
    monkeypatch.setattr(executor, "_fail_task", pause_after_failure)
    scan = asyncio.create_task(scheduler.recover_orphaned_tasks())
    try:
        await asyncio.wait_for(failed.wait(), 5)
        assert await scheduler.recover_orphaned_tasks() == 1
    finally:
        release.set()
        assert await scan == 1
    async with sessions() as db:
        task = await db.get(TaskRun, second)
        assert task.status == "pending" and task.recovery_count == 1
        assert len(list(await db.scalars(select(TaskEvent).where(TaskEvent.task_id == second)))) == 1


async def test_old_executor_snapshot_cannot_overwrite_concurrent_success(recovery_connections):
    """执行器持有旧 running 快照，另一连接成功后中断回收必须锁内看到终态。"""
    sessions, uid = recovery_connections
    tid = await stale_task(sessions, uid)
    async with sessions() as old:
        task = await old.get(TaskRun, tid)
        async with sessions() as winner:
            await winner.execute(update(TaskRun).where(TaskRun.id == tid).values(status="succeeded"))
            await winner.commit()
        await executor._requeue_interrupted_task(old, task)
    async with sessions() as db:
        task = await db.get(TaskRun, tid)
        assert task.status == "succeeded" and task.recovery_count == 0


@pytest.mark.parametrize("entry", ["api", "auto"])
async def test_force_same_params_waits_for_episode_before_locking_wallet(recovery_connections, monkeypatch, entry):
    """保存先持分集锁时，同 revision/params force 不能先占钱包形成反序。"""
    from app.api.drama.episodes import plan_episode_fragments
    from app.models_drama import DramaEpisode, DramaProject
    from app.schemas_drama import DramaPlanFragmentsRequest
    from app.services.drama.jobs import dispatch_episode_fragment_plan
    sessions, uid = recovery_connections
    async with sessions() as setup:
        project = DramaProject(user_id=uid)
        setup.add(project)
        await setup.flush()
        episode = DramaEpisode(project_id=project.id, name="锁序", params={
            "fragment_plan_status": "generating", "fragment_plan_revision": 0,
            "fragment_plan_mode": "llm", "fragment_plan_task_id": 42,
            **({"fragment_plan_auto": True} if entry == "auto" else {}),
        })
        setup.add(episode)
        await setup.commit()
        eid = episode.id
    entered = asyncio.Event()

    async def rates(*args):
        """真实估价边界作为 barrier；不请求外网。"""
        entered.set()
        return {}
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", rates)

    async def enqueue():
        """独立连接真实 force 入队。"""
        async with sessions() as db:
            user = await db.get(User, uid)
            if entry == "api":
                return await plan_episode_fragments(eid, DramaPlanFragmentsRequest(force=True), db, user)
            episode = await db.get(DramaEpisode, eid)
            return await dispatch_episode_fragment_plan(db, user, episode)
    async with sessions() as saving:
        await saving.scalar(select(DramaEpisode.id).where(DramaEpisode.id == eid).with_for_update())
        plan = asyncio.create_task(enqueue())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            # 真正的第二连接现在必须能拿钱包；旧实现已先锁 User，会立即报锁冲突。
            await saving.scalar(select(User.id).where(User.id == uid).with_for_update(nowait=True))
        finally:
            await saving.rollback()
            await asyncio.wait_for(plan, 5)


@pytest.mark.parametrize("entry", ["api", "auto"])
async def test_cold_plan_price_preparation_holds_no_episode_or_wallet_lock(recovery_connections, monkeypatch, entry):
    """冷价目外部等待期间另一真实连接可同时取得分集和钱包。"""
    from app.api.drama.episodes import plan_episode_fragments
    from app.models_drama import DramaEpisode, DramaProject
    from app.schemas_drama import DramaPlanFragmentsRequest
    sessions, uid = recovery_connections
    async with sessions() as setup:
        project = DramaProject(user_id=uid)
        setup.add(project)
        await setup.flush()
        episode = DramaEpisode(project_id=project.id, name="冷价目")
        setup.add(episode)
        await setup.commit()
        eid = episode.id
    calls = []

    async def cold_price(*args):
        """在真正估价调用点用独立连接 NOWAIT 检查全部相关锁。"""
        async with sessions() as observer:
            await observer.scalar(select(DramaEpisode.id).where(DramaEpisode.id == eid).with_for_update(nowait=True))
            await observer.scalar(select(User.id).where(User.id == uid).with_for_update(nowait=True))
        calls.append(True)
        return {}
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", cold_price)
    async with sessions() as db:
        user = await db.get(User, uid)
        if entry == "api":
            await plan_episode_fragments(eid, DramaPlanFragmentsRequest(force=True), db, user)
        else:
            from app.services.drama.jobs import dispatch_episode_fragment_plan
            await dispatch_episode_fragment_plan(db, user, await db.get(DramaEpisode, eid))
    assert calls == [True]


async def test_plan_failure_patch_releases_task_lock_and_preserves_new_force(recovery_connections, monkeypatch):
    """旧任务已提交失败后，新同 revision force 可入队；随后旧 patch 不改新 owner。"""
    from app.api.drama.episodes import plan_episode_fragments
    from app.models_drama import DramaEpisode, DramaProject
    from app.schemas_drama import DramaPlanFragmentsRequest
    sessions, uid = recovery_connections
    async with sessions() as setup:
        project = DramaProject(user_id=uid)
        setup.add(project)
        await setup.flush()
        ep = DramaEpisode(project_id=project.id, name="归属")
        setup.add(ep)
        await setup.flush()
        task = await make_task(setup, await setup.get(User, uid), domain="drama", task_type="fragment_plan", status="running")
        task.episode_id, task.drama_project_id, task.recovery_count = ep.id, project.id, 2
        task.payload = {"expected_revision": 0}
        ep.params = {"fragment_plan_status": "generating", "fragment_plan_revision": 0,
                     "fragment_plan_task_id": task.id}
        await setup.flush()
        await setup.execute(update(TaskRun).where(TaskRun.id == task.id).values(updated_at=datetime.now(UTC) - timedelta(hours=1)))
        await setup.commit()
        tid, eid = task.id, ep.id
    failed, release = asyncio.Event(), asyncio.Event()
    original = executor._fail_task

    async def pause(db, task, exc):
        """停在标准失败 commit 后、分集补丁前。"""
        await original(db, task, exc)
        failed.set()
        await asyncio.wait_for(release.wait(), 5)
    monkeypatch.setattr(executor, "_fail_task", pause)
    scan = asyncio.create_task(scheduler.recover_orphaned_tasks())
    try:
        await asyncio.wait_for(failed.wait(), 5)
        async with sessions() as probe:
            assert await probe.scalar(select(TaskRun.id).where(TaskRun.id == tid).with_for_update(nowait=True)) == tid
        async with sessions() as newer:
            result = await plan_episode_fragments(eid, DramaPlanFragmentsRequest(force=True), newer, await newer.get(User, uid))
            assert result.params["fragment_plan_task_id"] != tid
    finally:
        release.set()
        await scan
    async with sessions() as db:
        current = await db.get(DramaEpisode, eid)
        assert current.params["fragment_plan_status"] == "generating"
        assert current.params["fragment_plan_task_id"] != tid


async def test_auto_batch_locks_all_episodes_before_first_wallet(recovery_connections, monkeypatch):
    """自动多集批次先锁外准备一次价目，再锁全部分集；后续建档不会反向等分集。"""
    from sqlalchemy.exc import DBAPIError
    from app.models_drama import DramaEpisode, DramaProject, DramaScript
    from app.services.billing import settlement
    from app.services.drama import jobs
    sessions, uid = recovery_connections
    async with sessions() as db:
        project = DramaProject(user_id=uid)
        db.add(project)
        await db.flush()
        db.add(DramaScript(project_id=project.id, episode_content=[
            {"episodeNumber": 1, "body": "第一集正文"}, {"episodeNumber": 2, "body": "第二集正文"},
        ]))
        episodes = [DramaEpisode(project_id=project.id, name=str(n), params={"episodeNumber": n}) for n in (1, 2)]
        db.add_all(episodes)
        await db.commit()
        pid, ids = project.id, [ep.id for ep in episodes]
    price_calls, wallet_calls = [], []

    async def rates(*args):
        """价目边界所有分集与钱包都可由另一连接取得。"""
        async with sessions() as observer:
            await observer.execute(select(DramaEpisode.id).where(DramaEpisode.id.in_(ids)).with_for_update(nowait=True))
            await observer.scalar(select(User.id).where(User.id == uid).with_for_update(nowait=True))
        price_calls.append(True)
        return {}
    original = settlement._lock_user

    async def lock_wallet(db, user_id):
        """真正锁钱包之前，独立连接验证两集都已被批次事务持锁。"""
        for eid in ids:
            async with sessions() as observer:
                with pytest.raises(DBAPIError, match="lock"):
                    await observer.scalar(select(DramaEpisode.id).where(DramaEpisode.id == eid).with_for_update(nowait=True))
        wallet_calls.append(True)
        return await original(db, user_id)
    monkeypatch.setattr(jobs, "AsyncSessionLocal", sessions)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", rates)
    monkeypatch.setattr(settlement, "_lock_user", lock_wallet)
    result = await jobs.kickoff_fragment_plans(pid, reseed_assets=False)
    assert result["ok"] is True and result["queued_episode_ids"] == ids
    assert price_calls == [True] and len(wallet_calls) == 2
    async with sessions() as db:
        for eid in ids:
            ep = await db.get(DramaEpisode, eid)
            owner = await db.get(TaskRun, ep.params["fragment_plan_task_id"])
            assert owner.episode_id == eid and owner.payload["expected_revision"] == ep.revision


@pytest.mark.parametrize("entry", ["api", "auto", "batch"])
async def test_price_wait_cannot_adopt_new_revision_for_old_force(recovery_connections, monkeypatch, entry):
    """旧请求估价期间真实保存新稿，随后必须冲突/跳过，不能授权新 revision。"""
    from fastapi import HTTPException
    from app.api.drama.episodes import plan_episode_fragments, save_fragments
    from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject, DramaScript
    from app.schemas_drama import DramaPlanFragmentsRequest
    from app.services.drama import jobs
    from tests.test_drama_fragment_save import item, request
    sessions, uid = recovery_connections
    async with sessions() as db:
        project = DramaProject(user_id=uid)
        db.add(project)
        await db.flush()
        db.add(DramaScript(project_id=project.id, episode_content=[{"episodeNumber": 1, "body": "原剧本"}]))
        episode = DramaEpisode(project_id=project.id, name="原请求", params={"episodeNumber": 1})
        db.add(episode)
        await db.flush()
        fragment = DramaEpisodeFragment(episode_id=episode.id, content="旧稿")
        db.add(fragment)
        await db.commit()
        pid, eid, fid = project.id, episode.id, fragment.id
    pricing, resume = asyncio.Event(), asyncio.Event()

    async def price_barrier(*args):
        """旧请求读过 revision 后，停在锁外价目等待。"""
        pricing.set()
        await asyncio.wait_for(resume.wait(), 5)
        return {}
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", price_barrier)
    monkeypatch.setattr(jobs, "AsyncSessionLocal", sessions)

    async def old_request():
        """使用真实三种入队入口，HTTP 冲突由测试保留返回检查。"""
        if entry == "batch":
            return await jobs.kickoff_fragment_plans(pid, reseed_assets=False)
        async with sessions() as db:
            user = await db.get(User, uid)
            if entry == "auto":
                return await jobs.dispatch_episode_fragment_plan(db, user, await db.get(DramaEpisode, eid))
            try:
                return await plan_episode_fragments(eid, DramaPlanFragmentsRequest(force=True), db, user)
            except HTTPException as exc:
                await db.rollback()
                return exc
    pending = asyncio.create_task(old_request())
    try:
        await asyncio.wait_for(pricing.wait(), 5)
        async with sessions() as saved:
            fragment = await saved.get(DramaEpisodeFragment, fid)
            result = await save_fragments(eid, request([item(fragment, content="用户新稿")]), saved, await saved.get(User, uid))
            assert result.revision == 1
            current = await saved.get(DramaEpisode, eid)
            current.params = {**(current.params or {}), "user_setting": "保留新设置",
                              "fragment_plan_status": "completed", "fragment_plan_revision": 1}
            saved_params = dict(current.params)
            await saved.commit()
    finally:
        resume.set()
        result = await asyncio.wait_for(pending, 5)
    if entry == "api":
        assert isinstance(result, HTTPException) and result.status_code == 409
    elif entry == "auto":
        assert result is None
    else:
        assert result["queued_episode_ids"] == [] and eid in result["skipped_episode_ids"]
    async with sessions() as observer:
        current = await observer.get(DramaEpisode, eid)
        assert current.revision == 1 and current.params == saved_params
        assert (await observer.get(DramaEpisodeFragment, fid)).content == "用户新稿"
        assert not list(await observer.scalars(select(TaskRun.id).where(TaskRun.episode_id == eid)))
