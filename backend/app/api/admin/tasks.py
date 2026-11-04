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
from app.services.tasks.admin_events import AdminEventSummary, list_admin_task_events, load_admin_event_summaries
from app.services.tasks.runtime import runtime_summary
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
    summary = event_summary or AdminEventSummary()
    return AdminTaskRunOut(
        **fields,
        user_email=user_email,
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
        basis = resolve_billing_basis(estimated=bool(row.estimated), raw_usage_json=row.raw_usage_json)
        items.append(
            AdminUsageEventBriefOut(
                id=row.id,
                billing_key=row.billing_key,
                capability=row.capability,
                model=row.model or "",
                total_tokens=int(row.total_tokens or 0),
                charge_fen=int(row.charge_fen or 0),
                cost_fen=int(row.cost_fen or 0),
                estimated=bool(row.estimated),
                billing_basis=basis,
                billing_basis_label=billing_basis_label(basis),
                created_at=row.created_at,
            )
        )
    return items


@router.get("/tasks/stats", response_model=AdminTaskStatsOut)
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
        max_concurrency=int(settings.task_runtime_max_concurrency),
        scheduler=str(runtime.get("scheduler") or "stopped"),
        poller=str(runtime.get("poller") or "stopped"),
        watchdog=str(runtime.get("watchdog") or "stopped"),
        runtime_healthy=bool(runtime.get("healthy")),
        scheduler_success_age_sec=runtime.get("scheduler_success_age_sec"),
        scheduler_consecutive_failures=int(runtime.get("scheduler_consecutive_failures") or 0),
        fetched_at=datetime.now(UTC),
    )


@router.get("/tasks", response_model=AdminTaskListOut)
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


@router.get("/tasks/{task_id}", response_model=AdminTaskRunOut)
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


@router.get("/tasks/{task_id}/events", response_model=AdminTaskEventListOut)
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


@router.post("/tasks/{task_id}/cancel", response_model=AdminTaskRunOut)
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


@router.post('/tasks/{task_id}/billing-review', response_model=AdminTaskRunOut)
async def admin_review_task_billing(
    task_id: int,
    body: 'AdminTaskBillingReviewRequest',
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTaskRunOut:
    """人工核定未记账费用后结束本地处理；不改变上游任务或已有用量。"""
    import json
    from app.models import WalletLedger
    from app.services.billing.ephemeral import durable_voice_request_alive
    from app.services.billing.settlement import PreparedTaskSettlements, settle_task, finish_prepared_task_settlements
    from app.services.billing.usage import prepare_confirmed_cost_line
    from app.services.tasks import scheduler, poller
    from app.services.tasks.service import append_task_event, task_finalizing_window_open

    if not body.final_cost_confirmed or not body.note.strip():
        raise HTTPException(status_code=422, detail='请明确确认最终费用并填写核对依据')
    audit_note = json.dumps({'admin_id': _admin.id, 'missing_cost_fen': body.missing_cost_fen,
                             'note': body.note.strip()}, ensure_ascii=False)
    if len(audit_note) > 255:
        raise HTTPException(status_code=422, detail='核对依据过长，请精简后重试')
    # 先判断适用性，再沿创作 API 的目标→Task→钱包顺序尝试拿锁。
    # 旧执行器可能有相反锁序，因此所有新增等待使用 NOWAIT 并回滚返回 409。
    probe = await db.get(TaskRun, task_id)
    expected_review = bool(probe and probe.status == 'awaiting_review' and probe.billing_status in {'frozen', 'skipped', 'none'})
    if expected_review and probe.domain == 'kepu' and probe.project_id:
        from app.models import Project
        await _review_lock(db, select(Project).where(Project.id == probe.project_id))
    elif expected_review and probe.domain == 'drama' and probe.episode_id:
        from app.models_drama import DramaEpisode
        await _review_lock(db, select(DramaEpisode).where(DramaEpisode.id == probe.episode_id))
    task = await _review_lock(db, select(TaskRun).where(TaskRun.id == task_id))
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')
    prior = (await db.scalars(select(WalletLedger).where(
        WalletLedger.kind == 'billing_review', WalletLedger.ref_type == 'task_billing_review',
        WalletLedger.ref_id == str(task_id),
    ))).first()
    if prior:
        record = json.loads(prior.note)
        if record['missing_cost_fen'] != body.missing_cost_fen or record['note'] != body.note.strip():
            raise HTTPException(status_code=409, detail='本任务已按另一份核对结果结算，请刷新查看')
        await db.commit()
        return await admin_get_task(task_id, _admin, db)
    if not expected_review or task.status != 'awaiting_review' or task.billing_status not in {'frozen', 'skipped', 'none'}:
        raise HTTPException(status_code=409, detail='只能处理尚未结算的待核对任务')
    now = datetime.now(UTC)
    lease = task.lease_until
    if lease and lease.tzinfo is None:
        lease = lease.replace(tzinfo=UTC)
    cancel_job = scheduler._cancel_jobs.get(task_id)
    if (scheduler._job_alive(task_id) or (cancel_job and not cancel_job.done())
            or task_id in poller._poll_inflight or durable_voice_request_alive(task_id)
            or task_finalizing_window_open(task) or (lease and lease > now)):
        raise HTTPException(status_code=409, detail='本地请求或收尾仍在执行，请等待其退出后核对')
    # UsageEvent 的 User 外键也会加 KEY SHARE；先锁钱包，避免两个任务同时升级锁死锁。
    owner = await _review_lock(db, select(User).where(User.id == task.requested_by))
    events = list(await db.scalars(select(UsageEvent).where(
        UsageEvent.task_run_id == task_id, UsageEvent.settled.is_(False),
    )))
    if body.missing_cost_fen:
        usage = prepare_confirmed_cost_line(
            user_id=task.requested_by, task_run_id=task_id, cost_fen=body.missing_cost_fen,
            billing_key='manual_review', domain=task.domain, provider='manual_review',
            raw={'billing_basis': 'manual_review'}, project_id=task.project_id,
            drama_project_id=task.drama_project_id, shot_id=task.shot_id,
        )
        db.add(usage)
        await db.flush()
        events.append(usage)
    # 私有依据仅存在管理员账本中；用户事件只表达核对和本地结束。
    task.result_payload = {k: v for k, v in (task.result_payload or {}).items() if k not in {
        'video_billing_pending', 'video_submission_pending', 'tts_billing_pending', 'voice_billing_reservation',
    }}
    task.result_payload = {**task.result_payload, 'billing_review_resolved': True}
    task.status = 'failed'
    from app.models_tasks import TaskStep
    from sqlalchemy import update

    await db.execute(update(TaskStep).where(
        TaskStep.task_id == task_id, TaskStep.status.not_in(('done', 'failed', 'cancelled')),
    ).values(status='failed', finished_at=now, next_poll_at=None,
             error_code='billing_review_resolved', error_message='费用已核对，本地处理结束'))
    task.current_step_status = 'failed'
    task.error_code = 'billing_review_resolved'
    task.error_message = '费用已核对，本地任务已结束；原上游任务及已有媒体保留'
    task.finished_at = now
    task.next_action_at = task.lease_until = task.lease_token = None
    await db.flush()
    prepared = PreparedTaskSettlements(event_ids={task_id: {int(event.id) for event in events}}, newapi_logs={task_id: []})
    await settle_task(db, task_id, prepared_settlements=prepared)
    owner = await db.get(User, task.requested_by)
    db.add(WalletLedger(user_id=task.requested_by, delta_fen=0, balance_after=owner.balance_fen,
                        kind='billing_review', ref_type='task_billing_review', ref_id=str(task_id),
                        note=audit_note))
    await _close_review_business_state(db, task)
    if task.domain == 'studio' and task.task_type == 'tool_video' and task.provider_task_id:
        from app.services.studio_tools import update_tool_run_task

        await update_tool_run_task(db, task.requested_by, task.provider_task_id, {
            'status': 'failed', 'error': task.error_message,
        }, billing_task_id=task_id)
    await append_task_event(db, task_id, event_type='task.billing_review_resolved', status=task.status,
                            message='管理员已核对费用并完成结算，原任务记录已保留')
    await db.commit()
    await finish_prepared_task_settlements(db, prepared)
    return await admin_get_task(task_id, _admin, db)
