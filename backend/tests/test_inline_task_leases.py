"""HTTP task leases survive a second scheduler without replaying generation."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base
from app.models import UsageEvent, User, WalletLedger
from app.models_tasks import TaskEvent, TaskRun
from app.services.billing import ephemeral
from app.services.billing.settlement import freeze_for_task
from app.services.tasks import inline, scheduler


@pytest.fixture
async def inline_sessions(tmp_path, monkeypatch):
    """File SQLite has genuinely separate request, heartbeat and scheduler sessions."""
    import app.main  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "inline.db").as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(inline, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "_running_jobs", {})
    monkeypatch.setattr(scheduler, "_running_queues", {})
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    async with sessions() as db:
        user = User(email="inline@example.test", hashed_password="test", balance_fen=10000)
        db.add(user)
        await db.commit()
        user_id = user.id
    yield sessions, user_id
    await engine.dispose()


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@pytest.mark.parametrize("deferred", [False, True])
async def test_slow_inline_generation_renews_remote_lease_and_is_never_requeued(inline_sessions, monkeypatch, deferred):
    sessions, user_id = inline_sessions
    monkeypatch.setattr(inline, "INLINE_LEASE_SECONDS", 0.15)
    monkeypatch.setattr(inline, "INLINE_HEARTBEAT_SECONDS", 0.02)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def request():
        async with sessions() as db:
            user = await db.get(User, user_id)

            async def provider():
                nonlocal calls
                calls += 1
                started.set()
                await finish.wait()
                return "provider-result" if deferred else "完成的文案"

            run = ephemeral.run_billed_ephemeral_deferred if deferred else ephemeral.run_billed_ephemeral
            return await run(db, user, domain="drama", task_type="agent_chat", executor=provider)

    job = asyncio.create_task(request())
    try:
        await asyncio.wait_for(started.wait(), 3)
        async with sessions() as observer:
            task = (await observer.execute(select(TaskRun))).scalar_one()
            task_id, first_expiry = task.id, aware(task.lease_until)
            assert task.lease_token.startswith(inline.INLINE_LEASE_PREFIX)
            assert task.next_action_at is None and task.status == "running"
        # Initial admission is deliberately short; renewals use the production
        # window so SQLite/Windows scheduling jitter cannot expire the test owner.
        monkeypatch.setattr(inline, "INLINE_LEASE_SECONDS", 600)
        for _ in range(100):
            async with sessions() as observer:
                task = await observer.get(TaskRun, task_id)
                if aware(task.lease_until) > first_expiry + timedelta(seconds=1):
                    break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("independent inline heartbeat did not renew the lease")
        # The model outlives its original lease. Independent heartbeats keep it
        # valid; no scheduler-local HTTP job registration masks this regression.
        await asyncio.sleep(0.30)
        async with sessions() as observer:
            task = await observer.get(TaskRun, task_id)
            assert aware(task.lease_until) > first_expiry
            assert aware(task.lease_until) > datetime.now(UTC)
            task.updated_at = datetime.now(UTC) - timedelta(hours=1)
            await observer.commit()
        assert scheduler._running_jobs == {}
        assert await scheduler.recover_orphaned_tasks() == 0
        async with sessions() as observer:
            assert (await observer.get(TaskRun, task_id)).status == "running"
        finish.set()
        await asyncio.wait_for(job, 3)
        async with sessions() as observer:
            tasks = list((await observer.execute(select(TaskRun))).scalars())
            assert len(tasks) == 1 and calls == 1
            task = tasks[0]
            assert task.status == ("awaiting_poll" if deferred else "succeeded")
            assert task.lease_token is None and task.lease_until is None
    finally:
        finish.set()
        if not job.done():
            job.cancel()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_and_cancelled_inline_requests_release_leases(inline_sessions, cancel):
    sessions, user_id = inline_sessions

    async with sessions() as db:
        user = await db.get(User, user_id)

        async def provider():
            if cancel:
                raise asyncio.CancelledError()
            raise RuntimeError("provider unavailable")

        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await ephemeral.run_billed_ephemeral(db, user, domain="drama", task_type="agent_chat", executor=provider)
    async with sessions() as observer:
        task = (await observer.execute(select(TaskRun))).scalar_one()
        assert task.status == ("cancelled" if cancel else "failed")
        assert task.lease_token is None and task.lease_until is None
        assert task.finished_at is not None
    assert await scheduler.recover_orphaned_tasks() == 0


async def test_expired_inline_owner_fails_and_settles_once_without_replaying(inline_sessions, monkeypatch):
    sessions, user_id = inline_sessions
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    async with sessions() as db:
        task = TaskRun(requested_by=user_id, domain="drama", task_type="agent_chat", status="running", payload={})
        db.add(task)
        await db.flush()
        await freeze_for_task(db, task)
        assert task.billing_status == "frozen"
        db.add(UsageEvent(user_id=user_id, task_run_id=task.id, billing_key="llm_chat", charge_fen=25, settled=False))
        task.lease_token = inline.INLINE_LEASE_PREFIX + "stopped-process"
        task.lease_until = datetime.now(UTC) - timedelta(minutes=1)
        task.updated_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()
        task_id = task.id
    assert await scheduler.recover_orphaned_tasks() == 1
    assert await scheduler.recover_orphaned_tasks() == 0
    async with sessions() as observer:
        task = await observer.get(TaskRun, task_id)
        user = await observer.get(User, user_id)
        assert task.status == "failed" and task.error_code == "inline_execution_interrupted"
        assert task.next_action_at is None and task.lease_token is None and task.lease_until is None
        assert task.billing_status == "settled" and task.billing_charged_fen == 25
        assert user.balance_fen == 9975 and user.frozen_fen == 0
        events = list((await observer.execute(select(TaskEvent.event_type).where(TaskEvent.task_id == task_id))).scalars())
        assert events.count("task.failed") == 1 and "task.recovered" not in events
        charges = list((await observer.execute(select(WalletLedger).where(WalletLedger.user_id == user_id, WalletLedger.kind == "settle"))).scalars())
        assert len(charges) == 1
