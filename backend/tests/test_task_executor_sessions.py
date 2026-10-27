"""Long provider calls must release executor connections and reload current task state."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

import app.models_teams  # noqa: F401 — resolve team foreign keys for isolated SQLite DDL
from app.config import get_settings
from app.models import UsageEvent, User
from app.models_tasks import TaskEvent, TaskRun, TaskStep, TaskTarget
from app.services.tasks.executor import execute_task_run
from app.services.tasks.handlers import TaskHandler
from app.services.tasks.service import get_task_for_runtime


@pytest_asyncio.fixture
async def executor_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real, deliberately small pool; no application database is contacted."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'executor.sqlite3').as_posix()}",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.2,
    )
    models = (User, TaskRun, TaskStep, TaskTarget, TaskEvent, UsageEvent)
    async with engine.begin() as connection:
        for model in models:
            await connection.run_sync(model.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.tasks.executor.AsyncSessionLocal", sessions)
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    async with sessions() as db:
        db.add(User(id=1, email="executor@test.local", hashed_password="unused"))
        await db.commit()
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _new_task(sessions) -> int:
    async with sessions() as db:
        task = TaskRun(
            domain="tools", task_type="mock_delay", requested_by=1,
            status="leased", payload={},
        )
        db.add(task)
        await db.flush()
        db.add(TaskStep(task_id=task.id, step_key="main", step_type="job", status="pending"))
        await db.commit()
        return int(task.id)


async def test_concurrent_provider_waits_leave_connections_for_api_queries(
    executor_database, monkeypatch: pytest.MonkeyPatch,
):
    """Two long tasks must not consume both pooled connections while awaiting providers."""
    sessions = executor_database
    task_ids = [await _new_task(sessions) for _ in range(2)]
    entered = {task_id: asyncio.Event() for task_id in task_ids}
    release_provider = asyncio.Event()

    async def provider_wait(task):
        entered[task.id].set()
        await release_provider.wait()
        return {"ok": True}

    handler = TaskHandler("tools", "mock_delay", provider_wait)
    monkeypatch.setattr("app.services.tasks.executor.get_task_handler", lambda *_: handler)
    jobs = []
    try:
        for task_id in task_ids:
            jobs.append(asyncio.create_task(execute_task_run(task_id)))
            await asyncio.wait_for(entered[task_id].wait(), timeout=2)
        # This uses the same real pool as executors, like an authenticated API query.
        async with sessions() as api_db:
            rows = (await api_db.execute(select(TaskRun.id))).scalars().all()
            assert sorted(rows) == task_ids
    finally:
        release_provider.set()
        await asyncio.gather(*jobs)

    async with sessions() as db:
        statuses = list((await db.execute(select(TaskRun.status))).scalars())
        assert statuses == ["succeeded", "succeeded"]


@pytest.mark.parametrize(
    ("updates", "expected_status", "expected_step_status"),
    [
        ({"cancel_requested": True, "status": "cancel_requested"}, "cancelled", "done"),
        ({"status": "awaiting_poll"}, "awaiting_poll", "submitting"),
    ],
    ids=["cancellation-during-provider-call", "handler-registered-provider-poll"],
)
async def test_finalization_uses_changes_committed_by_another_session(
    executor_database, monkeypatch: pytest.MonkeyPatch,
    updates, expected_status: str, expected_step_status: str,
):
    """A fresh read must preserve provider IDs, cancellation and deferred polling state."""
    sessions = executor_database
    task_id = await _new_task(sessions)

    async def provider_result(task):
        async with sessions() as writer:
            await writer.execute(update(TaskRun).where(TaskRun.id == task.id).values(
                **updates, provider_task_id="provider-created-by-handler",
            ))
            await writer.commit()
        return {"ok": True}

    handler = TaskHandler("tools", "mock_delay", provider_result)
    monkeypatch.setattr("app.services.tasks.executor.get_task_handler", lambda *_: handler)
    await execute_task_run(task_id)

    async with sessions() as db:
        task = await get_task_for_runtime(db, task_id)
        assert task.status == expected_status
        assert task.provider_task_id == "provider-created-by-handler"
        assert task.cancel_requested is bool(updates.get("cancel_requested"))
        assert task.steps[0].status == expected_step_status
        if expected_status == "awaiting_poll":
            assert task.result_payload is None
            assert task.finished_at is None


async def test_provider_failure_still_marks_task_and_step_failed(
    executor_database, monkeypatch: pytest.MonkeyPatch,
):
    sessions = executor_database
    task_id = await _new_task(sessions)

    async def provider_failure(_task):
        raise RuntimeError("provider connection failed")

    handler = TaskHandler("tools", "mock_delay", provider_failure)
    monkeypatch.setattr("app.services.tasks.executor.get_task_handler", lambda *_: handler)
    await execute_task_run(task_id)

    async with sessions() as db:
        task = await get_task_for_runtime(db, task_id)
        assert task.status == "failed"
        assert task.error_code == "RuntimeError"
        assert "provider connection failed" in task.error_message
        assert task.steps[0].status == "failed"
        assert task.billing_status == "settled"


@pytest.mark.parametrize("cancel_requested", [False, True], ids=["shutdown", "user-cancel"])
async def test_interrupted_provider_wait_uses_latest_cancellation_and_preserves_provider_id(
    executor_database, monkeypatch: pytest.MonkeyPatch, cancel_requested: bool,
):
    sessions = executor_database
    task_id = await _new_task(sessions)
    entered_provider = asyncio.Event()
    release_provider = asyncio.Event()

    async def provider_wait(_task):
        entered_provider.set()
        await release_provider.wait()

    handler = TaskHandler("tools", "mock_delay", provider_wait)
    monkeypatch.setattr("app.services.tasks.executor.get_task_handler", lambda *_: handler)
    job = asyncio.create_task(execute_task_run(task_id))
    try:
        await asyncio.wait_for(entered_provider.wait(), timeout=2)
        async with sessions() as writer:
            await writer.execute(update(TaskRun).where(TaskRun.id == task_id).values(
                provider_task_id="accepted-before-interruption",
                cancel_requested=cancel_requested,
                status="cancel_requested" if cancel_requested else "running",
            ))
            await writer.commit()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    finally:
        release_provider.set()
        await asyncio.gather(job, return_exceptions=True)

    async with sessions() as db:
        task = await get_task_for_runtime(db, task_id)
        assert task.status == ("cancelled" if cancel_requested else "pending")
        assert task.provider_task_id == "accepted-before-interruption"
        if cancel_requested:
            assert task.steps[0].status == "cancelled"
            assert task.finished_at is not None
        else:
            assert task.steps[0].status == "pending"
            assert task.payload["provider_task_id"] == "accepted-before-interruption"
            assert task.next_action_at is not None
