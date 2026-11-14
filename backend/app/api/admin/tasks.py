# Admin unified task platform API
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_admin
from app.models import UsageEvent, User
from app.models_tasks import TaskRun
from app.schemas import PageMeta
from app.schemas_tasks import AdminTaskBillingReviewRequest, AdminTaskEventListOut, AdminTaskListOut, AdminTaskRunOut, AdminTaskStatsOut, AdminUsageEventBriefOut, TaskRunOut
from app.services.billing.display import billing_basis_label, resolve_billing_basis
from app.services.billing.review import resolve_billing_review
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


@router.post("/tasks/{task_id}/billing-review", response_model=AdminTaskRunOut,
    summary="核对待核对任务的费用",
    description="管理员填写核定成本、用户收费和核对依据后结束待核对任务，退回其余预扣。只能处理待核对状态的任务。",
    response_description="操作成功，返回结算后的任务详情",
)
async def admin_review_task_billing(
    task_id: int,
    body: AdminTaskBillingReviewRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskRunOut:
    # 待核对任务唯一的结算出口
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=422, detail="请填写核对依据")
    await _ensure_review_not_in_flight(db, task_id)
    # 按结算同一锁序（任务→钱包）NOWAIT 预占：旧执行器持锁时短暂 409 让管理员重试，
    # 不持新锁等待相反锁序；冲突时 _review_lock 已回滚释放已占的锁。
    locked = await _review_lock(db, select(TaskRun).where(TaskRun.id == task_id))
    if locked is not None:
        await _review_lock(db, select(User).where(User.id == locked.requested_by))
    try:
        task = await resolve_billing_review(
            db, task_id, admin_id=int(admin.id), cost_fen=body.cost_fen, charge_fen=body.charge_fen, note=note,
        )
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _after_billing_review(db, task)
    return await admin_get_task(task_id, admin, db)


async def _ensure_review_not_in_flight(db: AsyncSession, task_id: int) -> None:
    """本地轮询、配音预留、收尾窗口或租约未到期时不允许核对，避免与执行器同时结算。"""
    from app.services.billing.ephemeral import durable_voice_request_alive
    from app.services.tasks import poller, scheduler
    from app.services.tasks.service import task_finalizing_window_open

    task = await db.get(TaskRun, task_id)
    if task is None:
        return
    lease = task.lease_until
    if lease and lease.tzinfo is None:
        lease = lease.replace(tzinfo=UTC)
    cancel_job = scheduler._cancel_jobs.get(task_id)
    if ((cancel_job and not cancel_job.done()) or task_id in poller._poll_inflight
            or durable_voice_request_alive(task_id) or task_finalizing_window_open(task)
            or (lease and lease > datetime.now(UTC))):
        raise HTTPException(status_code=409, detail="本地请求或收尾仍在执行，请等待其退出后核对")


async def _after_billing_review(db: AsyncSession, task: TaskRun) -> None:
    """核对结算后收尾业务展示：结束对应分镜/资产/项目的生成中状态，同步工具视频记录。"""
    task = await db.get(TaskRun, task.id)
    if task is None:
        return
    await _close_review_business_state(db, task)
    if task.domain == "studio" and task.task_type == "tool_video" and task.provider_task_id:
        from app.services.studio_tools import update_tool_run_task

        from app.services.public_errors import public_message

        # 工具记录对用户可见：按错误码给固定“已核对”文案，不展示内部核对说明
        await update_tool_run_task(db, task.requested_by, task.provider_task_id, {
            "status": "failed", "error": public_message(task.error_message, code=task.error_code),
        }, billing_task_id=task.id)
    await db.commit()


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


async def _review_lock(db: AsyncSession, statement):
    """人工核对宁可短暂冲突重试，也不持新锁等待旧执行器的相反锁序。"""
    from sqlalchemy.exc import DBAPIError

    try:
        return (await db.scalars(statement.with_for_update(nowait=True)
                                 .execution_options(populate_existing=True))).first()
    except DBAPIError as exc:
        if getattr(exc.orig, 'sqlstate', None) == '55P03':
            await db.rollback()
            raise HTTPException(status_code=409, detail='任务或钱包正在处理，请稍后重试核对') from exc
        raise


async def _close_review_business_state(db: AsyncSession, task: TaskRun) -> None:
    """只结束当前目标的本地生成展示，已有新任务时不覆盖新状态或媒体。"""
    from sqlalchemy import or_
    from app.services.tasks.service import ACTIVE_TASK_STATUSES
    from app.services.tasks.executor import _fail_drama_asset_generation_if_needed, _fail_kepu_project_if_needed

    scope = None
    if task.domain == 'kepu' and task.project_id:
        scope = TaskRun.project_id == task.project_id
    elif task.domain == 'drama' and task.fragment_id:
        scope = TaskRun.fragment_id == task.fragment_id
    elif task.domain == 'drama' and task.asset_id:
        scope = TaskRun.asset_id == task.asset_id
    if scope is None:
        return
    other = await db.scalar(select(TaskRun.id).where(
        scope, TaskRun.id != task.id,
        or_(TaskRun.id > task.id, TaskRun.status.in_(tuple(ACTIVE_TASK_STATUSES))),
    ).limit(1))
    if other is not None:
        return
    if task.domain == 'kepu':
        await _fail_kepu_project_if_needed(db, task)
    elif task.fragment_id:
        from app.models_drama import DramaEpisodeFragment
        from app.services.drama.fragment_editing import patch_fragment_params
        from app.services.drama.generation import build_failed_generation_params

        fragment = await _review_lock(db, select(DramaEpisodeFragment).where(DramaEpisodeFragment.id == task.fragment_id))
        if fragment:
            await patch_fragment_params(db, fragment, {'generation': build_failed_generation_params(
                (fragment.params or {}).get('generation'), task.error_message,
            )})
    elif task.asset_id:
        from app.models_drama import DramaAsset
        await _review_lock(db, select(DramaAsset).where(DramaAsset.id == task.asset_id))
        await _fail_drama_asset_generation_if_needed(db, task, task.error_message)
