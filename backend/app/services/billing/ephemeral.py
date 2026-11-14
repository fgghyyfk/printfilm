# -*- coding: utf-8 -*-
"""轻量 TaskRun：同步执行并计费（聊天、API、工具）。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_tasks import TaskRun
from app.services.public_errors import public_error, public_message
from app.schemas_tasks import TaskCreateRequest, TaskEventCreate
from sqlalchemy import select

from app.config import get_settings
from app.services.billing.context import billing_scope
from app.services.billing.settlement import PreparedTaskSettlements, freeze_for_task, settle_task
from app.services.drama.voice_generation_policy import ensure_voice_generation_task_enabled
from app.services.tasks.handlers import get_task_handler
from app.services.tasks.inline import hold_inline_lease, start_inline_lease
from app.services.tasks.queues import task_queue
from app.services.tasks.service import append_task_event, build_task_event, build_task_step
from app.services.tasks.upstream import upstream_event_session

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 仅显式音色持久预扣后的 HTTP 执行协程；不占调度器槽位，也不得被当作孤儿重排。
_durable_voice_jobs: dict[int, asyncio.Task] = {}


def durable_voice_request_alive(task_id: int) -> bool:
    """判断已提交预扣的显式音色请求是否仍在本进程执行。"""
    job = _durable_voice_jobs.get(task_id)
    return bool(job and not job.done())


async def create_ephemeral_task_row(
    db: AsyncSession,
    user: User,
    *,
    domain: str,
    task_type: str,
    payload: dict | None = None,
    project_id: int | None = None,
    drama_project_id: int | None = None,
    shot_id: int | None = None,
    fragment_id: int | None = None,
    asset_id: int | None = None,
    episode_id: int | None = None,
) -> TaskRun:
    """创建并立即执行的轻量任务行（不经过调度器排队）。"""
    ensure_voice_generation_task_enabled(domain, task_type)
    payload = {key: value for key, value in (payload or {}).items() if not key.startswith("_team_")}
    if drama_project_id is not None:
        from app.services.tasks.team_scope import prepare_background_task
        user = await prepare_background_task(db, int(drama_project_id), user)
    from app.services.tasks.service import _validate_task_scope
    from app.services.billing.settlement import ensure_balance_for_task
    scope = TaskCreateRequest(domain=domain, task_type=task_type, project_id=project_id, drama_project_id=drama_project_id, shot_id=shot_id, fragment_id=fragment_id, asset_id=asset_id, episode_id=episode_id)
    await _validate_task_scope(db, user, scope)
    handler = get_task_handler(domain, task_type)
    planned = handler.plan_steps(TaskCreateRequest(domain=domain, task_type=task_type)) if handler else []
    first = planned[0] if planned else None
    now = datetime.now(UTC)
    task = TaskRun(
        domain=domain,
        task_type=task_type,
        queue_type=task_queue(task_type, payload),
        status="pending",
        priority=1,
        requested_by=user.id,
        cancelable=False,
        current_step_key=first.step_key if first else task_type,
        current_step_status="pending" if first else None,
        scheduled_at=now,
        next_action_at=now,
        payload=payload or {},
        project_id=project_id,
        drama_project_id=scope.drama_project_id,
        shot_id=shot_id,
        fragment_id=fragment_id,
        asset_id=asset_id,
        episode_id=scope.episode_id,
        team_id=scope.team_id,
    )
    from app.services.media_models.planning import prepare_task_media
    await prepare_task_media(db, task)
    if task.team_id is not None:
        task.billing_estimate_fen = await ensure_balance_for_task(db, user, task)
    db.add(task)
    await db.flush()
    if task.team_id is not None:
        from app.services.teams.edit_locks import claim_task_locks
        await claim_task_locks(db, task, user.id)
    if first:
        db.add(build_task_step(task.id, first))
    db.add(
        build_task_event(
            task.id,
            TaskEventCreate(
                event_type="task.created",
                status=task.status,
                phase=task.current_step_key,
                message=f"轻量任务 {task_type}",
                payload={"ephemeral": True},
            ),
        )
    )
    await db.flush()
    return task


async def run_billed_ephemeral(
    db: AsyncSession,
    user: User,
    *,
    domain: str,
    task_type: str,
    executor: Callable[[], Awaitable[T]],
    payload: dict | None = None,
    project_id: int | None = None,
    drama_project_id: int | None = None,
    shot_id: int | None = None,
    fragment_id: int | None = None,
    asset_id: int | None = None,
    episode_id: int | None = None,
    commit: bool = True,
    commit_before_execute: bool = False,
    billable: bool = True,
    result_payload_factory: Callable[[T], dict[str, Any]] | None = None,
) -> tuple[TaskRun, T]:
    """
    创建轻量 TaskRun → 预扣 → 在 billing_scope 内执行 → 结算。
    用于聊天、配音与工作室工具等同步调用。
    """
    if commit_before_execute and (not commit or (domain, task_type) != ("drama", "voice_synthesis")):
        raise ValueError("持久预扣仅用于显式音色合成")
    task = await create_ephemeral_task_row(
        db,
        user,
        domain=domain,
        task_type=task_type,
        payload=payload,
        project_id=project_id,
        drama_project_id=drama_project_id,
        shot_id=shot_id,
        fragment_id=fragment_id,
        asset_id=asset_id,
        episode_id=episode_id,
    )
    try:
        if billable:
            await freeze_for_task(db, task)
        else:
            # Auditions and personal legacy synthesis remain free. They still
            # share the durable task lifecycle without creating a new charge.
            task.billing_status = "skipped"
            task.billing_enabled_snapshot = False
    except ValueError as exc:
        task.status = "failed"
        task.error_code = public_error(exc).code
        task.error_message = public_message(exc)
        # 未预扣成功，保持 none，勿标 skipped（skipped 表示全局关闭计费）
        task.billing_status = "none"
        await db.flush()
        raise

    now = datetime.now(UTC)
    task.status = "running"
    task.started_at = now
    start_inline_lease(task, now)
    await append_task_event(
        db,
        task.id,
        event_type="task.started",
        status=task.status,
        message="轻量任务开始",
    )
    await db.flush()

    task_id = int(task.id)
    if commit_before_execute:
        # 显式音色：标记持久预留并登记执行协程，回收扫描据此不重排、不按零成本解冻。
        task.result_payload = {"voice_billing_reservation": True}
        job = asyncio.current_task()
        _durable_voice_jobs[task_id] = job
        job.add_done_callback(lambda _job: _durable_voice_jobs.pop(task_id, None))
    try:
        # Accepted work must be visible while the provider is still generating.
        # commit=False controls the caller's final success transaction only; task
        # admission is durable before any external I/O, as for team/media tasks.
        await db.commit()
    except BaseException:
        _durable_voice_jobs.pop(task_id, None)
        raise

    result: T
    try:
        async with hold_inline_lease(task), billing_scope(task.id, team_id=task.team_id, snapshot=task.billing_snapshot), upstream_event_session(db):
            result = await executor()
        task.status = "succeeded"
        task.progress_percent = 100
        task.finished_at = datetime.now(UTC)
        output = result_payload_factory(result) if result_payload_factory else _inline_result_payload(result)
        # 成功后移除显式音色的在途预留标记，否则结算会误判为费用待核对。
        kept = {k: v for k, v in (task.result_payload or {}).items() if k != "voice_billing_reservation"}
        task.result_payload = {**kept, "ok": True, **output}
        await append_task_event(
            db,
            task.id,
            event_type="task.completed",
            status=task.status,
            message="轻量任务完成",
        )
    except asyncio.CancelledError:
        await _cancel_inline_task(db, task, billable=billable)
        raise
    except Exception as exc:
        logger.exception("ephemeral task failed task_id=%s", task.id)
        from app.services.tts_billing import mark_tts_billing_pending

        if commit_before_execute:
            # HTTP已返回，费用记录已独立提交；仅移除inflight预留，真实pending仍保留。
            task.result_payload = {k: v for k, v in (task.result_payload or {}).items() if k != "voice_billing_reservation"}
        billing_pending = mark_tts_billing_pending(task, exc)
        from app.services.video_recovery import mark_video_billing_pending

        mark_video_billing_pending(task, exc)
        task.status = "failed"
        task.error_code = "TTSBillingPending" if billing_pending else "ephemeral_failed"
        task.error_message = public_message(exc)
        task.finished_at = datetime.now(UTC)
        await append_task_event(
            db,
            task.id,
            event_type="task.failed",
            status=task.status,
            message=public_message(exc),
        )
        if billable:
            await settle_task(db, task.id)
        # An outer request rollback must not erase an accepted failed task.
        await db.commit()
        _durable_voice_jobs.pop(task_id, None)
        raise

    if billable:
        await settle_task(db, task.id)
    if commit or task.team_id is not None:
        await db.commit()
    await db.refresh(task)
    _durable_voice_jobs.pop(task_id, None)
    return task, result


def _inline_result_payload(result: Any) -> dict[str, Any]:
    """Keep generated text rather than only Python's return type."""
    if isinstance(result, str):
        return {"kind": "text", "text_content": result}
    if isinstance(result, dict):
        text = result.get("content") or result.get("reply") or result.get("text_content")
        if isinstance(text, str):
            return {"kind": "text", "text_content": text, "data": result}
    return {}


async def _cancel_inline_task(db: AsyncSession, task: TaskRun, *, billable: bool = True) -> None:
    await db.refresh(task)
    if task.status in {"succeeded", "failed", "cancelled"}:
        return
    task.status = "cancelled"
    task.finished_at = datetime.now(UTC)
    task.next_action_at = None
    task.lease_token = None
    task.lease_until = None
    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    if payload.get("voice_billing_reservation"):
        # 显式音色 HTTP 被中断：付费请求可能已发出，与孤儿回收一致转为“费用未知待核对”，
        # 保留冻结；未计费（无冻结）时只移除在途标记。
        kept = {k: v for k, v in payload.items() if k != "voice_billing_reservation"}
        if billable and task.billing_status in {"frozen", "reserved"}:
            kept["tts_billing_pending"] = {"reason": "voice_request_interrupted", "cost_unknown": True}
        task.result_payload = kept
    await append_task_event(db, task.id, event_type="task.cancelled", status=task.status,
                            message="生成已取消")
    if billable:
        await settle_task(db, task.id)
    await db.commit()


async def settle_deferred_video_poll(
    db: AsyncSession,
    user: User,
    *,
    provider_task_id: str,
    poll_status: str,
    error: str | None = None,
    billing_task_id: int | None = None,
    usage_tokens: int = 0,
    upstream_cost_fen: int | None = None,
    completion_tokens: int = 0,
    raw_usage: dict | None = None,
    billing_key: str | None = None,
    result_data: dict | None = None,
    failure_code: str = "upstream_failed",
) -> PreparedTaskSettlements | None:
    """锁外准备费用，短事务幂等结算并投影工具记录；调用方提交后执行 finish。"""
    from app.models import UsageEvent
    from app.services.billing.newapi_bill import prepare_newapi_bill_logs
    from app.services.billing.settlement import _lock_task
    from app.services.drama.billing_util import (
        prepare_seedance_video_usage, record_seedance_video_usage, seedance_billing_key,
    )
    from app.services.studio_tools import update_tool_run_task
    from app.services.tokenfree_pricing import canonicalize_channel_model_id

    terminal = (poll_status or "").strip().lower()
    if terminal not in {"succeeded", "failed", "cancelled"}:
        return None
    stmt = select(TaskRun).where(
        TaskRun.requested_by == user.id,
        TaskRun.provider_task_id == provider_task_id.strip(),
        TaskRun.domain.in_(("api", "studio")),
    )
    if billing_task_id is not None:
        stmt = stmt.where(TaskRun.id == billing_task_id)
    probe = (await db.execute(stmt.order_by(TaskRun.id.desc()).limit(1))).scalar_one_or_none()
    if not probe or probe.status != "awaiting_poll" or probe.billing_status not in {"frozen", "reserved", "skipped"}:
        return None

    task_id = int(probe.id)
    from app.services.video_recovery import confirmed_video_cost_fen

    confirmed_cost = confirmed_video_cost_fen(raw_usage, upstream_cost_fen)
    cost_unknown = failure_code == "poll_timeout" or (
        terminal != "succeeded" and not int(usage_tokens or 0) and confirmed_cost is None)
    # 计费关闭（skipped）时没有冻结可保留：与 settle_task 一致按普通失败结束，不挂待核对
    if cost_unknown and probe.billing_status in {"frozen", "reserved"}:
        # 另一轮询或人工核对可能已收尾；迟到响应不能重新冻结已结算任务。
        probe = await _lock_task(db, task_id)
        if (not probe or probe.status != "awaiting_poll" or probe.billing_status not in {"frozen", "reserved", "skipped"}
                or probe.requested_by != user.id or probe.provider_task_id != provider_task_id.strip()):
            return None
        probe.result_payload = {**(probe.result_payload or {}), "video_billing_pending": {
            "reason": "poll_timeout" if failure_code == "poll_timeout" else "upstream_terminal_cost_unknown", "provider_task_id": provider_task_id,
        }}
        await settle_task(db, task_id)
        if probe.domain == "studio" and probe.task_type == "tool_video":
            await update_tool_run_task(db, user.id, provider_task_id, {
                "status": "awaiting_review", "error": "视频结果和费用待核对，请勿重新生成",
            }, billing_task_id=task_id)
        return None
    payload = probe.payload if isinstance(probe.payload, dict) else {}
    key = billing_key or seedance_billing_key(generate_audio=bool(payload.get("generate_audio", True)))
    raw_model = str(payload.get("model_id") or payload.get("model") or get_settings().model_video or "")
    model = canonicalize_channel_model_id(raw_model) or raw_model or "seedance-2.5"
    events = list((await db.scalars(select(UsageEvent).where(
        UsageEvent.task_run_id == task_id, UsageEvent.settled.is_(False),
    ))).all())
    prepared_usage = None
    async with billing_scope(task_id, team_id=probe.team_id, snapshot=probe.billing_snapshot):
        if terminal == "succeeded" or int(usage_tokens or 0) > 0 or confirmed_cost is not None:
            from app.services.ark import TaskResult

            result = TaskResult(status="succeeded", total_tokens=int(usage_tokens),
                                completion_tokens=int(completion_tokens or usage_tokens), raw_usage=raw_usage, upstream_cost_fen=confirmed_cost)
            prepared_usage = await prepare_seedance_video_usage(
                lookup_db=db, user_id=user.id, billing_key=key, model=model, domain=probe.domain,
                task_result=result, fallback_duration_sec=payload.get("duration"),
                resolution=str(payload.get("resolution") or ""), provider_task_id=provider_task_id,
                project_id=probe.project_id, drama_project_id=probe.drama_project_id, shot_id=probe.shot_id,
            )
    prepared = PreparedTaskSettlements(
        event_ids={task_id: {int(event.id) for event in events}},
        newapi_logs={task_id: await prepare_newapi_bill_logs(events + ([prepared_usage] if prepared_usage else []))},
    )
    # 网络、OSS、价目和账单已完成；之后只允许数据库操作。
    task = await _lock_task(db, task_id)
    if (not task or task.status != "awaiting_poll" or task.billing_status not in {"frozen", "reserved", "skipped"}
            or task.requested_by != user.id or task.provider_task_id != provider_task_id.strip()):
        return None
    if prepared_usage is not None:
        async with billing_scope(task_id, team_id=task.team_id, snapshot=task.billing_snapshot):
            usage = await record_seedance_video_usage(
                db, user_id=user.id, billing_key=key, model=model, domain=task.domain,
                provider_task_id=provider_task_id, prepared_usage=prepared_usage,
            )
        prepared.event_ids[task_id].add(int(usage.id))
    task.status = "succeeded" if terminal == "succeeded" else "failed"
    task.finished_at = datetime.now(UTC)
    diagnosis = public_error(error or "生成失败", code=failure_code if failure_code != "upstream_failed" else None)
    task.error_code = None if terminal == "succeeded" else diagnosis.code
    task.error_message = None if terminal == "succeeded" else diagnosis.message
    if terminal == "succeeded":
        task.progress_percent = 100
    data = {**(result_data or {}), "status": task.status, "error": task.error_message}
    # 只持久化用于恢复记录的有限结果，不保存上游整包响应。
    task.result_payload = {"video_terminal_confirmed": True, "status": task.status, "kind": "video", "urls": list(data.get("urls") or []),
                           "preview_url": data.get("preview_url"), "error": task.error_message}
    if task.domain == "studio" and task.task_type == "tool_video":
        await update_tool_run_task(db, user.id, provider_task_id, data, billing_task_id=task_id)
    await append_task_event(db, task_id, event_type="task.completed" if terminal == "succeeded" else "task.failed",
                            status=task.status, message="视频生成完成" if terminal == "succeeded" else task.error_message)
    await settle_task(db, task_id, prepared_settlements=prepared)
    return prepared
