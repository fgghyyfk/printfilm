"""Automatic creation must distinguish shutdown from user cancellation."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import ProjectStatus
from app.services import pipeline


def _session_factory(project):
    db = SimpleNamespace(get=AsyncMock(return_value=project), commit=AsyncMock())

    @asynccontextmanager
    async def factory():
        yield db

    return db, factory


@pytest.mark.asyncio
async def test_shutdown_preserves_project_and_resume_can_complete(monkeypatch):
    project = SimpleNamespace(status=ProjectStatus.IMAGING, progress=35, error_msg=None)
    db, factory = _session_factory(project)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock())
    monkeypatch.setattr(pipeline, "_resume_plan", AsyncMock(side_effect=asyncio.CancelledError()))
    events = AsyncMock()
    monkeypatch.setattr(pipeline, "publish_progress", events)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.run_pipeline(98761, phase="assets")

    assert project.status == ProjectStatus.IMAGING
    assert project.error_msg is None
    db.commit.assert_not_awaited()
    events.assert_not_awaited()

    monkeypatch.setattr(pipeline, "_resume_plan", AsyncMock(return_value=(True, True, True, True)))
    compose = AsyncMock()
    monkeypatch.setattr(pipeline, "_compose_stage", compose)
    monkeypatch.setattr(pipeline, "_final_url", AsyncMock(return_value="/final.mp4"))
    await pipeline.run_pipeline(98761, phase=None)
    compose.assert_awaited_once_with(98761)
    assert project.status == ProjectStatus.DONE


@pytest.mark.asyncio
async def test_explicit_cancel_remains_cancelled_and_propagates(monkeypatch):
    project = SimpleNamespace(status=ProjectStatus.IMAGING, error_msg=None)
    _, factory = _session_factory(project)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock(side_effect=pipeline.PipelineCancelled("cancel")))
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())

    with pytest.raises(pipeline.PipelineCancelled):
        await pipeline.run_pipeline(98762)

    assert project.status == ProjectStatus.CANCELLED
    assert project.error_msg == "用户取消"


@pytest.mark.asyncio
async def test_failure_after_cancel_does_not_report_success(monkeypatch):
    project = SimpleNamespace(status=ProjectStatus.IMAGING, error_msg=None)
    _, factory = _session_factory(project)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock(side_effect=RuntimeError("provider interrupted")))
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    pipeline._cancelled.add(98763)
    with pytest.raises(pipeline.PipelineCancelled):
        await pipeline.run_pipeline(98763)
    assert project.status == ProjectStatus.CANCELLED
    assert 98763 not in pipeline._cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize("has_image", [False, True])
async def test_image_text_refuses_missing_image_or_audio(monkeypatch, tmp_path, has_image):
    image = tmp_path / "shot.png"
    image.write_bytes(b"image")
    shot = SimpleNamespace(
        shot_no=1, duration=3.0, narration="测试旁白", video_url=None,
        audio_url=None, image_url="/image.png" if has_image else None,
    )
    project = SimpleNamespace(shots=[shot], output_ratio="9:16", template=None, pipeline_mode="image_text")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: project)))

    @asynccontextmanager
    async def factory():
        yield db

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr(pipeline, "_set_status", AsyncMock())
    monkeypatch.setattr(pipeline.storage, "project_dir", lambda _id: tmp_path)
    monkeypatch.setattr(pipeline.storage, "local_path_from_url", lambda url: image if url else None)
    compose = AsyncMock()
    monkeypatch.setattr(pipeline, "_run_ffmpeg_compose_with_retry", compose)

    with pytest.raises(RuntimeError, match="配音" if has_image else "图片"):
        await pipeline._compose_stage(98764)
    compose.assert_not_awaited()
