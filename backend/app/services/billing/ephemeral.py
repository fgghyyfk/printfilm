# -*- coding: utf-8 -*-
"""轻量 TaskRun：同步执行并计费（聊天、API、工具）。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_tasks import TaskRun
from app.schemas_tasks import TaskCreateRequest, TaskEventCreate
from sqlalchemy import select

from app.config import get_settings
from app.services.billing.context import billing_scope
from app.services.billing.settlement import freeze_for_task, settle_task
from app.services.tasks.handlers import get_task_handler
from app.services.tasks.inline import hold_inline_lease, start_inline_lease
from app.services.tasks.queues import task_queue
from app.services.tasks.service import append_task_event, build_task_event, build_task_step
from app.services.tasks.upstream import upstream_event_session

T = TypeVar("T")


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
    billable: bool = True,
    result_payload_factory: Callable[[T], dict[str, Any]] | None = None,
) -> tuple[TaskRun, T]:
    """
    创建轻量 TaskRun → 预扣 → 在 billing_scope 内执行 → 结算。
    用于聊天、开放 API、工作室工具等同步调用。
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
        if billable:
            await freeze_for_task(db, task)
        else:
            # Auditions and personal legacy synthesis remain free. They still
            # share the durable task lifecycle without creating a new charge.
            task.billing_status = "skipped"
            task.billing_enabled_snapshot = False
    except ValueError:
        task.status = "failed"
        task.error_code = "insufficient_balance"
        task.error_message = "余额不足"
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

    # Accepted work must be visible while the provider is still generating.
    # commit=False controls the caller's final success transaction only; task
    # admission is durable before any external I/O, as for team/media tasks.
    await db.commit()

    result: T
    try:
        async with hold_inline_lease(task), billing_scope(task.id, team_id=task.team_id, snapshot=task.billing_snapshot), upstream_event_session(db):
            result = await executor()
        task.status = "succeeded"
        task.progress_percent = 100
        task.finished_at = datetime.now(UTC)
        output = result_payload_factory(result) if result_payload_factory else _inline_result_payload(result)
        task.result_payload = {**(task.result_payload or {}), "ok": True, **output}
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
        task.status = "failed"
        task.error_code = "ephemeral_failed"
        task.error_message = str(exc)[:500]
        task.finished_at = datetime.now(UTC)
        await append_task_event(
            db,
            task.id,
            event_type="task.failed",
            status=task.status,
            message=str(exc)[:200],
        )
        if billable:
            await settle_task(db, task.id)
        # An outer request rollback must not erase an accepted failed task.
        await db.commit()
        raise

    if billable:
        await settle_task(db, task.id)
    if commit or task.team_id is not None:
        await db.commit()
    await db.refresh(task)
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
    await append_task_event(db, task.id, event_type="task.cancelled", status=task.status,
                            message="生成已取消")
    if billable:
        await settle_task(db, task.id)
    await db.commit()


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
    except ValueError:
        task.status = "failed"
        task.error_code = "insufficient_balance"
        task.error_message = "余额不足"
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
        message="轻量视频任务开始",
    )
    await db.flush()

    await db.commit()

    result: T
    try:
        async with hold_inline_lease(task), billing_scope(task.id, team_id=task.team_id, snapshot=task.billing_snapshot), upstream_event_session(db):
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
    except asyncio.CancelledError:
        await _cancel_inline_task(db, task)
        raise
    except Exception as exc:
        task.status = "failed"
        task.error_code = "ephemeral_failed"
        task.error_message = str(exc)[:500]
        task.finished_at = datetime.now(UTC)
        await append_task_event(
            db,
            task.id,
            event_type="task.failed",
            status=task.status,
            message=str(exc)[:200],
        )
        await settle_task(db, task.id)
        await db.commit()
        raise

    if commit or task.team_id is not None:
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
    completion_tokens: int = 0,
    raw_usage: dict | None = None,
    billing_key: str | None = None,
) -> None:
    """轮询终态后结算轻量视频任务：成功才记 seedance 用量。

    用行锁 + billing_status==frozen 门闩，避免客户端轮询与后台 poller 并发双记用量。
    """
    from app.services.billing.settlement import _lock_task

    terminal = (poll_status or "").strip().lower()
    if terminal in {"", "running", "queued"}:
        return

    task_id: int | None = None
    if billing_task_id:
        probe = await db.get(TaskRun, billing_task_id)
        if probe and int(probe.requested_by) == int(user.id):
            task_id = int(probe.id)
    if task_id is None and provider_task_id.strip():
        stmt = (
            select(TaskRun.id)
            .where(
                TaskRun.requested_by == user.id,
                TaskRun.provider_task_id == provider_task_id.strip(),
                TaskRun.billing_status == "frozen",
            )
            .order_by(TaskRun.id.desc())
            .limit(1)
        )
        task_id = (await db.execute(stmt)).scalar_one_or_none()
    if task_id is None:
        return

    # 原子门闩：仅 frozen 任务可进入结算；并发第二次拿到 settled 后直接返回
    task = await _lock_task(db, task_id)
    if not task or task.billing_status != "frozen":
        return
    if int(task.requested_by) != int(user.id):
        return

    now = datetime.now(UTC)
    if terminal == "succeeded":
        payload = task.payload if isinstance(task.payload, dict) else {}
        async with billing_scope(task.id, team_id=task.team_id, snapshot=task.billing_snapshot):
            from app.services.drama.billing_util import record_seedance_video_usage, seedance_billing_key

            generate_audio = bool(payload.get("generate_audio", True))
            key = billing_key or seedance_billing_key(generate_audio=generate_audio)
            task_result = None
            if usage_tokens > 0 or (task.billing_snapshot or {}).get("mode") == "fixed_media":
                from app.services.ark import TaskResult

                task_result = TaskResult(
                    status="succeeded",
                    total_tokens=int(usage_tokens),
                    completion_tokens=int(completion_tokens or usage_tokens),
                    raw_usage=raw_usage,
                )
            from app.services.tokenfree_pricing import canonicalize_channel_model_id

            raw_billing = str(payload.get("model_id") or payload.get("model") or "").strip()
            if raw_billing:
                billing_model = canonicalize_channel_model_id(raw_billing) or raw_billing
            else:
                billing_model = canonicalize_channel_model_id(
                    str(get_settings().model_video or "").strip()
                ) or "seedance-2.5"
            await record_seedance_video_usage(
                db,
                user_id=user.id,
                billing_key=key,
                model=billing_model,
                domain=task.domain or "api",
                task_result=task_result,
                fallback_duration_sec=payload.get("duration"),
                resolution=str(payload.get("resolution") or ""),
                provider_task_id=provider_task_id,
                project_id=task.project_id,
                drama_project_id=task.drama_project_id,
                shot_id=task.shot_id,
            )
        task.status = "succeeded"
        task.progress_percent = 100
        task.finished_at = now
        task.error_code = None
        task.error_message = None
        await append_task_event(
            db,
            task.id,
            event_type="task.completed",
            status=task.status,
            message="视频生成完成",
        )
    else:
        task.status = "failed"
        task.error_code = "upstream_failed"
        task.error_message = (error or "生成失败")[:500]
        task.finished_at = now
        await append_task_event(
            db,
            task.id,
            event_type="task.failed",
            status=task.status,
            message=task.error_message,
        )

    await settle_task(db, task.id)
