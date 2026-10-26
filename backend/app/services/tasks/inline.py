"""Renewable leases for accepted tasks owned by an HTTP coroutine."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.database import AsyncSessionLocal
from app.models_tasks import TaskRun

logger = logging.getLogger("app.tasks.inline")
INLINE_LEASE_PREFIX = "inline:"
INLINE_LEASE_SECONDS = 600
INLINE_HEARTBEAT_SECONDS = 10


def start_inline_lease(task: TaskRun, now: datetime) -> None:
    """Persist with admission, before the first provider call can begin."""
    task.lease_token = INLINE_LEASE_PREFIX + uuid.uuid4().hex
    task.lease_until = now + timedelta(seconds=INLINE_LEASE_SECONDS)
    task.next_action_at = None


@asynccontextmanager
async def hold_inline_lease(task: TaskRun):
    """A separate short transaction keeps long requests alive across workers."""
    owner = asyncio.current_task()
    heartbeat = asyncio.create_task(_renew_inline_lease(task.id, task.lease_token, owner))
    try:
        yield
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        task.lease_token = None
        task.lease_until = None


async def _renew_inline_lease(task_id: int, token: str, owner: asyncio.Task) -> None:
    while True:
        await asyncio.sleep(INLINE_HEARTBEAT_SECONDS)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(update(TaskRun).where(
                    TaskRun.id == task_id, TaskRun.lease_token == token,
                    TaskRun.status == "running",
                ).values(lease_until=datetime.now(UTC) + timedelta(seconds=INLINE_LEASE_SECONDS)))
                await db.commit()
                if not result.rowcount:
                    # Another worker already terminated the expired owner. Never
                    # let its stale coroutine submit further work or settle twice.
                    owner.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("inline task heartbeat failed task_id=%s", task_id)
