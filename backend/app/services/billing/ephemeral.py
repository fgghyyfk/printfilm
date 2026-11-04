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
from app.services.tasks.handlers import get_task_handler
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
    handler = get_task_handler(domain, task_type)
    planned = handler.plan_steps(TaskCreateRequest(domain=domain, task_type=task_type)) if handler else []
    first = planned[0] if planned else None
    now = datetime.now(UTC)
    task = TaskRun(
        domain=domain,
        task_type=task_type,
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
        drama_project_id=drama_project_id,
        shot_id=shot_id,
        fragment_id=fragment_id,
        asset_id=asset_id,
        episode_id=episode_id,
    )
    db.add(task)
    await db.flush()
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
) -> tuple[TaskRun, T]:
    """
    创建轻量 TaskRun → 预扣 → 在 billing_scope 内执行 → 结算。
    用于聊天、开放 API、工作室工具等同步调用。
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
        await freeze_for_task(db, task)
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
        task.result_payload = {"voice_billing_reservation": True}
        job = asyncio.current_task()
        _durable_voice_jobs[task_id] = job
        job.add_done_callback(lambda _job: _durable_voice_jobs.pop(task_id, None))
        try:
            # 付费外调之前必须真实提交 TaskRun + 冻结；失败不进入 executor。
            await db.commit()
        except BaseException:
            _durable_voice_jobs.pop(task_id, None)
            raise

    result: T
    try:
        async with billing_scope(task.id), upstream_event_session(db):
            result = await executor()
        task.status = "succeeded"
        task.progress_percent = 100
        task.finished_at = datetime.now(UTC)
        task.result_payload = {"ok": True} if result is None else {"ok": True, "result": str(type(result))}
        await append_task_event(
            db,
            task.id,
            event_type="task.completed",
            status=task.status,
            message="轻量任务完成",
        )
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
        await settle_task(db, task.id)
        if commit:
            await db.commit()
        _durable_voice_jobs.pop(task_id, None)
        raise

    await settle_task(db, task.id)
    if commit:
        await db.commit()
    await db.refresh(task)
    _durable_voice_jobs.pop(task_id, None)
    return task, result


def _extract_provider_task_id(result: Any) -> str:
    """从 executor 返回值解析上游 task_id。"""
    if isinstance(result, dict):
        raw = result.get("task_id") or result.get("provider_task_id") or ""
        return str(raw).strip()
    return str(result).strip()


async def run_billed_ephemeral_deferred(
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
) -> tuple[TaskRun, T]:
    """
    轻量 TaskRun：预扣 → 提交上游异步任务 → 保持 awaiting_poll，轮询终态后再结算。
    用于开放 API / 工作室生视频，避免提交成功即扣费。
    """
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
        await freeze_for_task(db, task)
    except ValueError as exc:
        task.status = "failed"
        task.error_code = public_error(exc).code
        task.error_message = public_message(exc)
        task.billing_status = "none"
        await db.flush()
        raise

    now = datetime.now(UTC)
    task.status = "running"
    task.started_at = now
    await append_task_event(
        db,
        task.id,
        event_type="task.started",
        status=task.status,
        message="轻量视频任务开始",
    )
    await db.flush()

    result: T
    try:
        async with billing_scope(task.id), upstream_event_session(db):
            result = await executor()
        provider_id = _extract_provider_task_id(result)
        if not provider_id:
            raise RuntimeError("上游未返回 task_id")
        task.status = "awaiting_poll"
        task.provider_task_id = provider_id
        task.progress_percent = 30
        task.next_action_at = now
        task.result_payload = {"awaiting_poll": True, "provider_task_id": provider_id}
        await append_task_event(
            db,
            task.id,
            event_type="task.submitted",
            status=task.status,
            message="已提交上游，等待轮询",
            payload={"provider_task_id": provider_id},
        )
    except Exception as exc:
        logger.exception("ephemeral task failed task_id=%s", task.id)
        from app.services.video_recovery import mark_video_billing_pending

        mark_video_billing_pending(task, exc)
        task.status = "failed"
        task.error_code = "ephemeral_failed"
        task.error_message = public_message(exc)
        task.finished_at = datetime.now(UTC)
        await append_task_event(
            db,
            task.id,
            event_type="task.failed",
            status=task.status,
            message=public_message(exc),
        )
        await settle_task(db, task.id)
        if commit:
            await db.commit()
        raise

    if commit:
        await db.commit()
    await db.refresh(task)
    return task, result


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
    if not probe or probe.status != "awaiting_poll" or probe.billing_status not in {"frozen", "skipped"}:
        return None

    task_id = int(probe.id)
    from app.services.video_recovery import confirmed_video_cost_fen

    confirmed_cost = confirmed_video_cost_fen(raw_usage, upstream_cost_fen)
    if failure_code == "poll_timeout" or (terminal != "succeeded" and not int(usage_tokens or 0) and confirmed_cost is None):
        # 另一轮询或人工核对可能已收尾；迟到响应不能重新冻结已结算任务。
        probe = await _lock_task(db, task_id)
        if (not probe or probe.status != "awaiting_poll" or probe.billing_status not in {"frozen", "skipped"}
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
    async with billing_scope(task_id):
        if terminal == "succeeded" or int(usage_tokens or 0) > 0 or confirmed_cost is not None:
            from app.services.ark import TaskResult

            result = TaskResult(status="succeeded", total_tokens=int(usage_tokens),
                                completion_tokens=int(completion_tokens or usage_tokens), raw_usage=raw_usage, upstream_cost_fen=confirmed_cost)
            prepared_usage = await prepare_seedance_video_usage(
                user_id=user.id, billing_key=key, model=model, domain=probe.domain,
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
    if (not task or task.status != "awaiting_poll" or task.billing_status not in {"frozen", "skipped"}
            or task.requested_by != user.id or task.provider_task_id != provider_task_id.strip()):
        return None
    if prepared_usage is not None:
        async with billing_scope(task_id):
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
