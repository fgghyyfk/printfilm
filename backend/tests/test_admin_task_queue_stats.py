"""Queue monitoring aggregates use an isolated in-memory database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine

from app.services.tasks.service import get_task_stats_admin


async def _queue_stats(rows):
    # Only the columns read by monitoring are needed; no application DB connection.
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    task_runs = Table(
        "task_runs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("domain", String),
        Column("status", String),
        Column("next_action_at", DateTime),
    )
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            if rows:
                connection.execute(task_runs.insert(), rows)
            db = SimpleNamespace(execute=AsyncMock(side_effect=connection.execute))
            return await get_task_stats_admin(db)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_pending_stats_distinguish_activation_from_due_and_delayed_tasks():
    now = datetime.now(UTC)
    rows = [
        {"domain": "drama", "status": "pending", "next_action_at": now - timedelta(seconds=120)},
        {"domain": "tools", "status": "pending", "next_action_at": now - timedelta(seconds=30)},
        {"domain": "drama", "status": "pending", "next_action_at": None},
        {"domain": "drama", "status": "pending", "next_action_at": now + timedelta(minutes=5)},
        {"domain": "drama", "status": "running", "next_action_at": now - timedelta(minutes=10)},
        {"domain": "drama", "status": "cancel_requested", "next_action_at": None},
    ]

    stats = await _queue_stats(rows)

    assert stats["pending_count"] == 4
    assert stats["pending_ready_count"] == 2
    assert stats["pending_deferred_count"] == 1
    assert stats["pending_delayed_count"] == 1
    assert 120 <= stats["oldest_ready_wait_sec"] <= 122
    assert stats["running_count"] == 1
    assert stats["cancel_requested_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("include_deferred", [False, True])
async def test_no_due_tasks_reports_zero_wait_age(include_deferred):
    rows = []
    if include_deferred:
        rows = [
            {"domain": "drama", "status": "pending", "next_action_at": None},
            {"domain": "drama", "status": "pending", "next_action_at": datetime.now(UTC) + timedelta(hours=1)},
        ]

    stats = await _queue_stats(rows)

    assert stats["pending_ready_count"] == 0
    assert stats["oldest_ready_wait_sec"] == 0
    assert stats["pending_count"] == stats["pending_deferred_count"] + stats["pending_delayed_count"]
