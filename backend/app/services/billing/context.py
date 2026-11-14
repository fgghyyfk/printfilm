# -*- coding: utf-8 -*-
"""任务计费上下文：当前 task_run_id。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

_current_task_run_id: ContextVar[int | None] = ContextVar("billing_task_run_id", default=None)
_current_team_id: ContextVar[int | None] = ContextVar("billing_team_id", default=None)


def get_current_task_run_id() -> int | None:
    return _current_task_run_id.get()


def get_current_team_id() -> int | None:
    return _current_team_id.get()


def set_current_team_id(team_id: int | None) -> None:
    _current_team_id.set(team_id)


def set_current_task_run_id(task_run_id: int | None) -> None:
    _current_task_run_id.set(task_run_id)


@asynccontextmanager
async def billing_scope(task_run_id: int | None, *, team_id: int | None = None, snapshot: dict | None = None) -> AsyncIterator[None]:
    # Nested contexts for the same task retain its team unless explicitly supplied.
    current_team = _current_team_id.get() if _current_task_run_id.get() == task_run_id else None
    from app.services.media_models.runtime import current_snapshot, current_operation
    inherited = current_snapshot.get() if _current_task_run_id.get() == task_run_id else None
    media_token = current_snapshot.set(snapshot if snapshot is not None else inherited)
    operation_token = current_operation.set(None)
    token = _current_task_run_id.set(task_run_id)
    team_token = _current_team_id.set(team_id if team_id is not None else current_team)
    try:
        yield
    finally:
        current_operation.reset(operation_token)
        current_snapshot.reset(media_token)
        _current_team_id.reset(team_token)
        _current_task_run_id.reset(token)
