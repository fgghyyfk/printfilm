"""管理端事件有界投影与历史分页；保留数据库中的完整追加式日志。"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_tasks import TaskEvent, TaskRun


@dataclass
class AdminEventSummary:
    """任务响应中的有界事件及完整历史计数，不修改 ORM 事件关系。"""

    events: list[TaskEvent] = field(default_factory=list)
    events_total: int = 0
    upstream_events: list[TaskEvent] = field(default_factory=list)
    upstream_events_total: int = 0


async def load_admin_event_summaries(
    db: AsyncSession,
    task_ids: list[int],
    *,
    include_recent: bool = False,
) -> dict[int, AdminEventSummary]:
    """批量加载上游事件摘要；单条详情另附最近 100 条普通事件。"""
    # 列表每任务至多 20 条上游事件，详情至多 200 条，噪声生命周期日志不占此额度。
    task_ids = list(dict.fromkeys(task_ids))
    summaries = {task_id: AdminEventSummary() for task_id in task_ids}
    if not task_ids:
        return summaries
    if include_recent and len(task_ids) != 1:
        raise ValueError("详情事件仅支持单条任务")
    upstream_filter = TaskEvent.event_type.startswith("upstream.request.", autoescape=True)
    count_rows = (
        await db.execute(
            select(TaskEvent.task_id, func.count(), func.count().filter(upstream_filter))
            .where(TaskEvent.task_id.in_(task_ids))
            .group_by(TaskEvent.task_id)
        )
    ).all()
    for task_id, total, upstream_total in count_rows:
        summaries[task_id].events_total = int(total)
        summaries[task_id].upstream_events_total = int(upstream_total)

    # 在数据库中按任务截断，避免把整页所有历史实体加载进 Python。
    ranked = (
        select(
            TaskEvent.id.label("event_id"),
            func.row_number().over(partition_by=TaskEvent.task_id, order_by=TaskEvent.id.desc()).label("event_rank"),
        )
        .where(TaskEvent.task_id.in_(task_ids), upstream_filter)
        .subquery()
    )
    upstream_rows = (
        await db.execute(
            select(TaskEvent)
            .join(ranked, ranked.c.event_id == TaskEvent.id)
            .where(ranked.c.event_rank <= (200 if include_recent else 20))
            .order_by(TaskEvent.id.desc())
        )
    ).scalars().all()
    for event in upstream_rows:
        summaries[event.task_id].upstream_events.append(event)

    if include_recent:
        summaries[task_ids[0]].events = list(
            (
                await db.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_ids[0])
                    .order_by(TaskEvent.id.desc())
                    .limit(100)
                )
            ).scalars().all()
        )
    return summaries


async def list_admin_task_events(
    db: AsyncSession,
    task_id: int,
    *,
    page: int,
    page_size: int,
    event_type_prefix: str | None = None,
) -> tuple[list[TaskEvent], int]:
    """按 ID 倒序读取单条任务完整历史，支持上游事件前缀过滤。"""
    # 先校验任务存在；查询始终有 task_id 与服务端分页上限。
    exists = (await db.execute(select(TaskRun.id).where(TaskRun.id == task_id))).scalar_one_or_none()
    if exists is None:
        raise LookupError("任务不存在")
    filters = [TaskEvent.task_id == task_id]
    if event_type_prefix:
        filters.append(TaskEvent.event_type.startswith(event_type_prefix, autoescape=True))
    total = int((await db.execute(select(func.count()).select_from(TaskEvent).where(*filters))).scalar_one() or 0)
    rows = (
        await db.execute(
            select(TaskEvent)
            .where(*filters)
            .order_by(TaskEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return list(rows), total
