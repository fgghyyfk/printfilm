"""Studio integration tests: real SQL and lifecycle, isolated local database, no providers."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_db
from app.deps import get_current_user
from app.models import ToolRun, User
from app.models import UsageEvent
from app.models_tasks import TaskRun, TaskStep
from app.services import studio_generation as generation
from app.services.tasks import executor, scheduler
from app.services.tasks.queues import task_queue


@pytest.fixture
async def studio(monkeypatch):
    # Register FKs without starting the API or connecting to its configured database.
    import app.main  # noqa: F401
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(generation, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(executor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    monkeypatch.setattr(generation, "ensure_public_urls", lambda urls: list(urls or []))
    async with sessions() as db:
        user = User(email="studio@example.test", hashed_password="test", balance_fen=100000)
        other = User(email="other@example.test", hashed_password="test", balance_fen=100000)
        db.add_all([user, other])
        await db.commit()
    yield sessions, user, other
    await engine.dispose()


@pytest.mark.parametrize("kind,payload,queue", [
    ("script_summary", {}, "text"), ("voice_synthesis", {}, "text"), ("tool_audio", {}, "text"),
    ("asset_image", {}, "image"), ("tool_image", {}, "image"), ("shot_regen_image", {}, "image"),
    ("fragment_video", {}, "video"), ("asset_video", {}, "video"), ("v1_seedance", {}, "video"),
    ("project_pipeline", {"phase": "script"}, "text"), ("project_pipeline", {"phase": "assets"}, "image"),
    ("project_pipeline", {"phase": "videos"}, "video"),
])
def test_queue_routing(kind, payload, queue):
    assert task_queue(kind, payload) == queue


async def enqueue(sessions, user, tool_id="t2i"):
    async with sessions() as db:
        data = await generation.enqueue_generation(db, user, tool_id=tool_id, prompt="A sunny forest",
            params={"duration": "5s", "voice": "narrator_calm"}, files=[])
        await db.commit()
    return data


async def test_submission_is_durable_before_generation(studio, monkeypatch):
    sessions, user, _ = studio
    provider = AsyncMock()
    monkeypatch.setattr(generation, "run_image_tool", provider)
    data = await enqueue(sessions, user)
    async with sessions() as db:
        task = await db.get(TaskRun, data["platform_task_id"])
        row = await db.get(ToolRun, data["run_id"])
        assert task.queue_type == "image" and task.status == "pending"
        assert task.next_action_at is not None
        assert row.task_id == f"task-{task.id}"
        assert row.params["billing_task_id"] == task.id
        assert (await db.execute(select(TaskStep).where(TaskStep.task_id == task.id))).scalar_one()
    provider.assert_not_awaited()


async def test_image_execution_uses_one_task_and_reuses_saved_result(studio, monkeypatch):
    sessions, user, _ = studio
    provider = AsyncMock(return_value={"kind": "image", "urls": ["https://cdn.test/image.png"], "status": "succeeded"})
    monkeypatch.setattr(generation, "run_image_tool", provider)
    data = await enqueue(sessions, user)
    await executor.execute_task_run(data["platform_task_id"])
    async with sessions() as db:
        row = await db.get(ToolRun, data["run_id"])
        task = await generation.sync_generation_record(db, row)
        assert task.status == row.status == "succeeded"
        assert row.urls == ["https://cdn.test/image.png"]
        assert task.progress_percent == 100 and task.finished_at is not None
        assert len((await db.execute(select(TaskRun))).scalars().all()) == 1
        await generation.execute_generation(task)
    provider.assert_awaited_once()


async def test_audio_runs_on_text_queue_and_returns_audio(studio, monkeypatch):
    sessions, user, _ = studio
    tts = AsyncMock(return_value="https://cdn.test/narration.mp3")
    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(tts=tts))
    usage = AsyncMock()
    monkeypatch.setattr("app.services.billing.record_line", usage)
    data = await enqueue(sessions, user, "audio")
    await executor.execute_task_run(data["platform_task_id"])
    async with sessions() as db:
        row = await db.get(ToolRun, data["run_id"])
        task = await generation.sync_generation_record(db, row)
        assert task.queue_type == "text" and row.status == "succeeded"
        assert row.urls == ["https://cdn.test/narration.mp3"]
    assert tts.await_args.kwargs["shot_no"] == data["run_id"]
    usage.assert_awaited_once()


async def test_video_resumes_existing_provider_id_and_polls_without_billing(studio, monkeypatch):
    sessions, user, _ = studio
    submit = AsyncMock(return_value={"task_id": "upstream-video-123", "preview_url": "https://cdn.test/frame.png"})
    poll = AsyncMock(return_value={"status": "succeeded", "kind": "video", "urls": ["https://cdn.test/movie.mp4"], "usage": {"total_tokens": 100}})
    usage = AsyncMock()
    monkeypatch.setattr(generation, "start_video_tool", submit)
    monkeypatch.setattr(generation, "poll_video_task", poll)
    monkeypatch.setattr("app.services.drama.billing_util.record_seedance_video_usage", usage)
    data = await enqueue(sessions, user, "t2v")
    task_id = data["platform_task_id"]
    await executor.execute_task_run(task_id)
    async with sessions() as db:
        task = await db.get(TaskRun, task_id)
        assert task.status == "awaiting_poll" and task.provider_task_id == "upstream-video-123"
        await generation.execute_generation(task)
    submit.assert_awaited_once()
    await generation.poll_generation_video(task_id)
    await generation.poll_generation_video(task_id)
    async with sessions() as db:
        row = await db.get(ToolRun, data["run_id"])
        task = await db.get(TaskRun, task_id)
        assert row.status == task.status == "succeeded"
        assert row.urls == ["https://cdn.test/movie.mp4"]
        assert task.provider_status == "succeeded"
    poll.assert_awaited_once()
    usage.assert_awaited_once()


async def test_failed_generation_remains_visible_in_history(studio, monkeypatch):
    sessions, user, _ = studio
    monkeypatch.setattr(generation, "run_image_tool", AsyncMock(side_effect=RuntimeError("provider unavailable")))
    data = await enqueue(sessions, user)
    await executor.execute_task_run(data["platform_task_id"])
    async with sessions() as db:
        row = await db.get(ToolRun, data["run_id"])
        await generation.sync_generation_record(db, row)
        assert row.status == "failed" and "provider unavailable" in row.error


async def test_full_image_lane_does_not_block_text_or_video(studio, monkeypatch):
    sessions, user, _ = studio
    settings = get_settings()
    for queue in ("text", "image", "video"):
        monkeypatch.setattr(settings, f"task_queue_{queue}_concurrency", 1)
    monkeypatch.setattr(settings, "task_user_max_concurrency", 1)
    monkeypatch.setattr(scheduler, "_running_jobs", {})
    monkeypatch.setattr(scheduler, "_running_queues", {})
    monkeypatch.setattr(scheduler, "_cancel_jobs", {})
    monkeypatch.setattr(scheduler, "_run_one", AsyncMock())
    await enqueue(sessions, user, "t2i")
    await enqueue(sessions, user, "t2i")
    await enqueue(sessions, user, "audio")
    await enqueue(sessions, user, "t2v")
    await scheduler._tick()
    await asyncio.sleep(0)
    async with sessions() as db:
        rows = (await db.execute(select(TaskRun))).scalars().all()
        assert sorted(task.queue_type for task in rows if task.status == "leased") == ["image", "text", "video"]
        assert sum(task.status == "pending" for task in rows) == 1


async def test_balance_failure_before_provider_is_exposed(studio, monkeypatch):
    sessions, user, _ = studio
    data = await enqueue(sessions, user)
    provider = AsyncMock()
    monkeypatch.setattr(generation, "run_image_tool", provider)
    monkeypatch.setattr(executor, "freeze_for_task", AsyncMock(side_effect=ValueError("insufficient balance")))
    await executor.execute_task_run(data["platform_task_id"])
    async with sessions() as db:
        row = await db.get(ToolRun, data["run_id"])
        task = await generation.sync_generation_record(db, row)
        assert task.error_code == "insufficient_balance" and row.status == "failed"
    provider.assert_not_awaited()


async def test_generation_freezes_and_settles_one_task_once(studio, monkeypatch):
    from app.services.billing import record_line
    sessions, user, _ = studio
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    async def provider(db, owner, **_kwargs):
        await record_line(db, user_id=owner.id, billing_key="seedream", model="test-image", tokens=45000,
                          estimated=True, domain="studio")
        return {"kind": "image", "status": "succeeded", "urls": ["https://cdn.test/paid.png"]}
    generate = AsyncMock(side_effect=provider)
    monkeypatch.setattr(generation, "run_image_tool", generate)
    data = await enqueue(sessions, user)
    await executor.execute_task_run(data["platform_task_id"])
    async with sessions() as db:
        task = await db.get(TaskRun, data["platform_task_id"])
        owner = await db.get(User, user.id)
        assert task.billing_status == "settled"
        assert owner.frozen_fen == 0
        assert owner.balance_fen == 100000 - task.billing_charged_fen
        await generation.execute_generation(task)
        events = (await db.execute(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).scalars().all()
        assert len(events) == 1
    generate.assert_awaited_once()


async def test_history_filters_counts_provider_id_and_ownership(studio):
    from app.api.tools import router
    sessions, user, other = studio
    first = await enqueue(sessions, user)
    await enqueue(sessions, user, "audio")
    hidden = await enqueue(sessions, other)
    async with sessions() as db:
        task = await db.get(TaskRun, first["platform_task_id"])
        task.status = "succeeded"
        task.provider_task_id = "find-upstream-id"
        await db.commit()
    api = FastAPI()
    api.include_router(router, prefix="/api")
    api.dependency_overrides[get_current_user] = lambda: user
    async def database():
        async with sessions() as db:
            yield db
    api.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = (await client.get("/api/tools/runs?kind=image&status=succeeded&task_id=find-upstream-id")).json()
        assert result["total"] == 1 and result["counts"] == {"succeeded": 1, "all": 1}
        assert result["items"][0]["platform_task_id"] == first["platform_task_id"]
        assert "file_paths" not in result["items"][0]["params"]
        assert (await client.get(f"/api/tools/tasks/{hidden['task_id']}")).status_code == 404
        assert (await client.get(f"/api/tools/runs/{hidden['run_id']}")).status_code == 404
        assert (await client.get("/api/tools/runs?kind=unsupported")).status_code == 422
        assert (await client.get("/api/tools/runs?created_from=2026-10-22T00:00:00Z&created_to=2026-10-20T00:00:00Z")).status_code == 400


async def test_live_remote_lease_is_not_recovered(studio, monkeypatch):
    sessions, user, _ = studio
    data = await enqueue(sessions, user)
    async with sessions() as db:
        task = await db.get(TaskRun, data["platform_task_id"])
        task.status = "running"
        task.updated_at = datetime.now(UTC) - timedelta(minutes=20)
        task.lease_until = datetime.now(UTC) + timedelta(minutes=2)
        await db.commit()
    monkeypatch.setattr(scheduler, "_running_jobs", {})
    assert await scheduler.recover_orphaned_tasks() == 0
    async with sessions() as db:
        task = await db.get(TaskRun, data["platform_task_id"])
        task.lease_until = datetime.now(UTC) - timedelta(minutes=1)
        task.updated_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()
    assert await scheduler.recover_orphaned_tasks() == 1
