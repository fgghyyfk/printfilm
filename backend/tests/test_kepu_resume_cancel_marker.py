"""A fresh wizard task can resume after an earlier task was cancelled before start."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import ProjectStatus
from app.services import pipeline
from app.services.tasks import handlers


@pytest.mark.parametrize("handler_name,stage_name", [
    ("_run_kepu_project_pipeline", "run_pipeline"), ("_run_kepu_compose", "compose_only"),
])
async def test_fresh_task_clears_previous_marker_in_executing_worker(monkeypatch, handler_name, stage_name):
    task = SimpleNamespace(project_id=97110, cancel_requested=False, payload={"phase": "script"})
    pipeline.cancel_pipeline(task.project_id)

    async def stage(project_id, **_kwargs):
        assert not pipeline.is_cancelled(project_id)

    monkeypatch.setattr(pipeline, stage_name, stage)
    monkeypatch.setattr(handlers, "_kepu_output_snapshot", AsyncMock(return_value={}))
    result = await getattr(handlers, handler_name)(task)
    assert result["ok"]


@pytest.mark.parametrize("handler_name,stage_name", [
    ("_run_kepu_project_pipeline", "run_pipeline"), ("_run_kepu_compose", "compose_only"),
])
async def test_current_cancellation_is_not_cleared_or_executed(monkeypatch, handler_name, stage_name):
    task = SimpleNamespace(project_id=97111, cancel_requested=True, payload={"phase": "script"})
    pipeline.cancel_pipeline(task.project_id)
    stage = AsyncMock()
    monkeypatch.setattr(pipeline, stage_name, stage)
    try:
        result = await getattr(handlers, handler_name)(task)
        assert result["cancelled"] and pipeline.is_cancelled(task.project_id)
        stage.assert_not_awaited()
    finally:
        pipeline.clear_pipeline_cancellation(task.project_id)


async def test_db_cancellation_still_blocks_when_snapshot_predates_current_request(monkeypatch):
    task = SimpleNamespace(project_id=97112, cancel_requested=False, payload={})
    project = SimpleNamespace(status=ProjectStatus.CANCELLED)

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(get=AsyncMock(return_value=project))

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    try:
        with pytest.raises(pipeline.PipelineCancelled):
            await handlers._run_kepu_compose(task)
        assert project.status == ProjectStatus.CANCELLED
    finally:
        pipeline.clear_pipeline_cancellation(task.project_id)
