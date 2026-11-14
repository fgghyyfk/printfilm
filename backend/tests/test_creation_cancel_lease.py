"""Cross-process cancellation cannot settle while a media writer owns its lease."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.tasks import scheduler


@pytest.mark.parametrize("execution_mode", ["direct", "queued"])
async def test_cross_process_cancel_waits_for_any_live_executor_lease(monkeypatch, execution_mode):
    task = SimpleNamespace(
        status="cancel_requested", payload={}, execution_mode=execution_mode,
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
    )

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(commit=AsyncMock())

    settle = AsyncMock()
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", session)
    monkeypatch.setattr(scheduler, "get_task_for_runtime", AsyncMock(return_value=task))
    monkeypatch.setattr(scheduler, "_mark_cancelled", settle)
    await scheduler._run_cancel_request(12345)
    settle.assert_not_awaited()
    task.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await scheduler._run_cancel_request(12345)
    settle.assert_awaited_once()
