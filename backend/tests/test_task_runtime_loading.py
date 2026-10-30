"""执行器读取任务时无需载入不断增长的审计历史。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.models_tasks import TaskEvent, TaskRun, TaskStep, TaskTarget
import app.models_teams  # noqa: F401 — resolve TaskRun.team_id before isolated table DDL
from app.services.tasks.service import get_task_for_runtime


async def test_runtime_loads_steps_and_targets_without_loading_event_history():
    """历史激活事件再多，也不能让每次领取/轮询重新载入整段历史。"""
    engine = create_engine("sqlite:///:memory:")
    try:
        for model in (TaskRun, TaskStep, TaskTarget, TaskEvent):
            model.__table__.create(engine)
        with Session(engine) as db:
            db.add(TaskRun(id=1, requested_by=1, domain="drama", task_type="fragment_video"))
            db.add_all(TaskEvent(task_id=1, event_type="task.activated") for _ in range(1000))
            db.commit()
        with Session(engine) as db:
            async_db = SimpleNamespace(execute=AsyncMock(side_effect=db.execute))
            task = await get_task_for_runtime(async_db, 1)
            assert task is not None
            assert "events" in inspect(task).unloaded
            assert task.steps == []
            assert task.targets == []
    finally:
        engine.dispose()
