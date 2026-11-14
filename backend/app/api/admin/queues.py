# Admin task runtime monitoring API (legacy /queues path for dashboard compat)
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_admin
from app.models import User
from app.models_tasks import TaskRun
from app.schemas import AdminQueuesOut
from app.services.tasks.runtime import runtime_summary
from app.services.tasks.service import get_task_stats_admin
from app.services.tasks.queues import QUEUE_LABELS, QUEUE_TYPES, queue_capacity
from app.services.tasks.execution_policy import llm_task_condition

router = APIRouter()


@router.get("/queues", response_model=AdminQueuesOut,
    summary="查看任务队列概况",
    description="管理员查看统一任务平台的排队、租约、运行数量与各队列容量。此为兼容旧管理页面的入口；detail 为保留参数，任务明细请使用 /api/admin/tasks。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_queues(
    detail: bool = Query(default=False, description="保留参数，任务中心请用 /admin/tasks"),
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminQueuesOut:
    # 兼容旧路径：返回 task_runs 聚合统计，不再暴露历史 worker 控制项
    _ = detail
    settings = get_settings()
    stats = await get_task_stats_admin(db)
    runtime = runtime_summary()
    running_jobs = int(runtime.get("scheduler_running_jobs") or 0)
    max_concurrency = sum(queue_capacity(settings, queue) for queue in QUEUE_TYPES)
    queue_rows = (await db.execute(select(TaskRun.queue_type, TaskRun.status, func.count())
                                  .where(~llm_task_condition(TaskRun))
                                  .group_by(TaskRun.queue_type, TaskRun.status))).all()
    counts = {(queue, status): count for queue, status, count in queue_rows}
    pending = int(stats.get("pending_count") or 0)
    active = int(stats.get("active_count") or 0)
    return AdminQueuesOut.model_validate(
        {
            "ok": True,
            "redis_ok": False,
            "unacked": 0,
            "total_pending": pending,
            "active_count": active,
            "reserved_count": int(stats.get("leased_count") or 0),
            "queues": [
                {
                    "name": queue,
                    "label": QUEUE_LABELS[queue],
                    "pending": counts.get((queue, "pending"), 0),
                    "active": sum(counts.get((queue, state), 0) for state in ("leased", "running", "awaiting_poll")),
                    "max_concurrency": queue_capacity(settings, queue),
                    "sample": [],
                }
                for queue in QUEUE_TYPES
            ],
            "pending_tasks": [],
            "active_tasks": [],
            "reserved_tasks": [],
            "runtime": {"mode": "task_runtime", "running_jobs": running_jobs, "max_concurrency": max_concurrency},
            "fetched_at": datetime.now(UTC).isoformat(),
        }
    )
