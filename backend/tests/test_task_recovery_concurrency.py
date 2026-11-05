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
