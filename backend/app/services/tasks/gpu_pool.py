"""GPU 长任务槽位池：数字人对口型这类单条要跑 1–3 小时的任务，不占全站普通槽位。

- 普通池：全站 task_runtime_max_concurrency、单用户 task_user_max_concurrency
- GPU 池：全站 task_gpu_max_concurrency、单用户 task_gpu_user_max_concurrency
数字人分段是「提交一段 → 等完 → 下一段」，单用户只占 1 个 GPU 槽位时，各用户的分段会轮流进入 GPU 队列。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.models_tasks import TaskRun

# 整条全自动出片
GPU_TASK_TYPES: frozenset[tuple[str, str]] = frozenset({("kepu", "avatar_auto")})


def is_gpu_task(domain: str | None, task_type: str | None, payload: dict[str, Any] | None) -> bool:
    """该任务是否走 GPU 长任务池：全自动出片，或数字人项目的「视频」阶段。"""
    key = ((domain or "").strip(), (task_type or "").strip())
    if key in GPU_TASK_TYPES:
        return True
    data = payload if isinstance(payload, dict) else {}
    return key == ("kepu", "project_pipeline") and data.get("pipeline_mode") == "avatar" \
        and data.get("phase") == "videos"


def gpu_task_clause(model: Any = TaskRun) -> ColumnElement[bool]:
    """与 is_gpu_task 等价的 SQL 条件（model 可传 aliased(TaskRun)）。"""
    typed = [and_(model.domain == d, model.task_type == t) for d, t in sorted(GPU_TASK_TYPES)]
    avatar_videos = and_(
        model.domain == "kepu",
        model.task_type == "project_pipeline",
        # payload 缺字段时 ->> 为 NULL，NOT NULL 仍是 NULL 会让任务两个池都领不到，统一 coalesce 成空串
        func.coalesce(model.payload["pipeline_mode"].as_string(), "") == "avatar",
        func.coalesce(model.payload["phase"].as_string(), "") == "videos",
    )
    return or_(*typed, avatar_videos)


def pool_limits(gpu: bool) -> tuple[int, int]:
    """返回 (全站上限, 单用户上限)。"""
    s = get_settings()
    if gpu:
        return max(1, int(s.task_gpu_max_concurrency)), max(1, int(s.task_gpu_user_max_concurrency))
    return max(1, int(s.task_runtime_max_concurrency)), max(1, int(s.task_user_max_concurrency))
