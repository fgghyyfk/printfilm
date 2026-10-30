# Admin unified task platform API
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_admin
from app.models import UsageEvent, User
from app.schemas import PageMeta
from app.schemas_tasks import AdminTaskEventListOut, AdminTaskListOut, AdminTaskRunOut, AdminTaskStatsOut, AdminUsageEventBriefOut, TaskRunOut
from app.services.billing.display import billing_basis_label, resolve_billing_basis
from app.services.tasks.admin_events import AdminEventSummary, list_admin_task_events, load_admin_event_summaries
from app.services.tasks.runtime import runtime_summary
from app.services.tasks.queues import QUEUE_TYPES, queue_capacity, queue_stats, task_queue
from app.services.tasks.service import cancel_task_admin, get_task_admin, get_task_stats_admin, list_tasks_admin

router = APIRouter()


def _task_to_admin_out(
    task,
    user_email: str | None,
    usage_lines: list[AdminUsageEventBriefOut] | None = None,
    *,
    event_summary: AdminEventSummary | None = None,
) -> AdminTaskRunOut:
    """组装有界响应，不读取或替换 ORM events，避免全历史加载与孤儿删除。"""
    # 单独提取其余字段，Pydantic 不会访问被禁用的 events 懒加载关系。
    fields = {name: getattr(task, name) for name in TaskRunOut.model_fields if name != "events"}
    fields["queue_type"] = fields.get("queue_type") or task_queue(task.task_type, task.payload)
    summary = event_summary or AdminEventSummary()
    return AdminTaskRunOut(
        **fields,
        user_email=user_email,
        billing_snapshot=getattr(task, "billing_snapshot", None),
        usage_lines=usage_lines or [],
        events=summary.events,
        events_total=summary.events_total,
        upstream_events=summary.upstream_events,
        upstream_events_total=summary.upstream_events_total,
    )


async def _load_usage_lines(db: AsyncSession, task_id: int) -> list[AdminUsageEventBriefOut]:
    rows = (
        await db.execute(
            select(UsageEvent)
            .where(UsageEvent.task_run_id == task_id)
            .order_by(UsageEvent.id.asc())
        )
    ).scalars().all()
    items: list[AdminUsageEventBriefOut] = []
    for row in rows:
        basis = "fixed_media" if (row.billing_detail or {}).get("mode") == "fixed_media" else resolve_billing_basis(estimated=bool(row.estimated), raw_usage_json=row.raw_usage_json)
        items.append(
            AdminUsageEventBriefOut(
                id=row.id,
                billing_detail=row.billing_detail,
                billing_key=row.billing_key,
                capability=row.capability,
                model=row.model or "",
                total_tokens=int(row.total_tokens or 0),
                charge_fen=int(row.charge_fen or 0),
                estimated=bool(row.estimated),
                billing_basis=basis,
                billing_basis_label=billing_basis_label(basis),
                created_at=row.created_at,
            )
        )
    return items


@router.get("/tasks/stats", response_model=AdminTaskStatsOut,
    summary="查看全站任务统计",
    description="管理员查看全站任务状态分布、队列容量以及调度器、轮询器和看门狗的运行状态与健康摘要。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_task_stats(
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskStatsOut:
    # 任务平台聚合统计 + 运行时摘要
    stats = await get_task_stats_admin(db)
    runtime = runtime_summary()
    settings = get_settings()
    return AdminTaskStatsOut(
        **stats,
        scheduler_running_jobs=int(runtime.get("scheduler_running_jobs") or 0),
        max_concurrency=sum(queue_capacity(settings, queue) for queue in QUEUE_TYPES),
        queues=await queue_stats(db, settings),
        scheduler=str(runtime.get("scheduler") or "stopped"),
        poller=str(runtime.get("poller") or "stopped"),
        watchdog=str(runtime.get("watchdog") or "stopped"),
        runtime_healthy=bool(runtime.get("healthy")),
        scheduler_success_age_sec=runtime.get("scheduler_success_age_sec"),
        scheduler_consecutive_failures=int(runtime.get("scheduler_consecutive_failures") or 0),
        fetched_at=datetime.now(UTC),
    )


@router.get("/tasks", response_model=AdminTaskListOut,
    summary="分页查询全站任务",
    description="管理员按领域、状态、任务类型、发起用户、关键词及是否进行中筛选任务。返回分页信息与有界事件摘要，完整事件历史需单独分页查询。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    domain: str | None = None,
    status: str | None = None,
    task_type: str | None = None,
    user_id: int | None = None,
    q: str | None = None,
    active_only: bool = False,
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskListOut:
    # 分页列出全站 task_runs，支持领域/状态/用户筛选
    rows, total = await list_tasks_admin(
        db,
        page=page,
        page_size=page_size,
        domain=domain,
        status=status,
        task_type=task_type,
        user_id=user_id,
        q=q,
        active_only=active_only,
    )
    summaries = await load_admin_event_summaries(db, [task.id for task, _email in rows])
    items = [_task_to_admin_out(task, email, event_summary=summaries[task.id]) for task, email in rows]
    return AdminTaskListOut(
        items=items,
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


@router.get("/tasks/{task_id}", response_model=AdminTaskRunOut,
    summary="查看全站任务详情",
    description="管理员查看指定任务的执行信息、计费快照、用量行与近期事件窗口；完整事件历史通过任务事件接口获取。任务不存在时返回 404。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_get_task(
    task_id: int,
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskRunOut:
    # 查看任务详情；事件仅返回近期窗口，完整历史另行分页。
    try:
        task = await get_task_admin(db, task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    email = (
        await db.execute(select(User.email).where(User.id == task.requested_by))
    ).scalar_one_or_none()
    usage_lines = await _load_usage_lines(db, task.id)
    summary = (await load_admin_event_summaries(db, [task.id], include_recent=True))[task.id]
    return _task_to_admin_out(task, email, usage_lines, event_summary=summary)


@router.get("/tasks/{task_id}/events", response_model=AdminTaskEventListOut,
    summary="分页查询任务事件",
    description="管理员分页读取指定任务的完整事件历史；event_type_prefix 可筛选事件类型前缀。任务不存在时返回 404。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_list_task_events(
    task_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type_prefix: str | None = Query(None, min_length=1, max_length=64),
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskEventListOut:
    """分页读取完整事件历史，查询上游请求时可指定事件类型前缀。"""
    try:
        rows, total = await list_admin_task_events(
            db, task_id, page=page, page_size=page_size, event_type_prefix=event_type_prefix,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AdminTaskEventListOut(items=rows, total=total, page=page, page_size=page_size)


@router.post("/tasks/{task_id}/cancel", response_model=AdminTaskRunOut,
    summary="取消全站指定任务",
    description="管理员请求取消指定任务并返回最新任务详情。任务不存在时返回 404，任务当前状态不允许取消时返回 400。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_cancel_task(
    task_id: int,
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskRunOut:
    # 管理员取消任意进行中的任务
    try:
        task = await cancel_task_admin(db, task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    email = (
        await db.execute(select(User.email).where(User.id == task.requested_by))
    ).scalar_one_or_none()
    usage_lines = await _load_usage_lines(db, task.id)
    summary = (await load_admin_event_summaries(db, [task.id], include_recent=True))[task.id]
    return _task_to_admin_out(task, email, usage_lines, event_summary=summary)
