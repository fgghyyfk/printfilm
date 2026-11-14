"""视频提交或轮询结果未知时保留冻结待核对，禁止把超时当免费失败全额退款。"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.database import AsyncSessionLocal
from app.models_tasks import TaskRun
from app.services.billing.context import get_current_task_run_id

logger = logging.getLogger(__name__)

# 任务 result_payload 上的标记键；settle_task 见到它就转待核对。
VIDEO_PENDING_KEY = "video_billing_pending"
_NOTE_TIMEOUT_SEC = 5.0


class VideoBillingPending(RuntimeError):
    """上游可能已接受或仍在生成，结果和费用都不确定。"""

    def __init__(self, reason: str, *, provider_task_id: str | None = None):
        super().__init__("视频结果和费用待核对，已保留原任务，请勿重新生成")
        self.reason = reason
        self.provider_task_id = provider_task_id


class VideoSubmissionRejected(RuntimeError):
    """上游明确400拒绝，未创建任务；与提交结果未知严格区分。"""


class VideoGenerationFailed(RuntimeError):
    def __init__(self, result, *, generate_audio=None, model=None):
        super().__init__(result.error or "视频生成失败")
        self.result = result
        self.generate_audio = generate_audio
        self.model = model


def _pending_payload(reason: str, provider_task_id: str | None) -> dict:
    """构造写入 result_payload 的有限快照。"""
    return {"reason": reason[:100], "provider_task_id": provider_task_id}


def mark_video_billing_pending(task: TaskRun, exc: BaseException) -> bool:
    """沿异常链查找待核对错误并写到任务上；调用方改写异常时也能识别。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, VideoBillingPending):
            task.result_payload = {
                **(task.result_payload or {}),
                VIDEO_PENDING_KEY: _pending_payload(current.reason, current.provider_task_id),
            }
            if current.provider_task_id:
                task.provider_task_id = current.provider_task_id
            return True
        current = current.__cause__ or current.__context__
    return False


async def _write_note(task_id: int, reason: str, provider_task_id: str | None) -> None:
    """在独立短事务写标记，不依赖调用方的事务和异常传递。"""
    async with AsyncSessionLocal() as db:
        task = await db.get(TaskRun, task_id)
        if task is None:
            return
        task.result_payload = {
            **(task.result_payload or {}),
            VIDEO_PENDING_KEY: _pending_payload(reason, provider_task_id),
        }
        if provider_task_id:
            task.provider_task_id = provider_task_id
        await db.commit()


async def raise_video_billing_pending(
    reason: str, *, provider_task_id: str | None = None, cause: BaseException | None = None,
) -> None:
    """把未知结果立刻记到当前任务并抛出；不少调用方会捕获异常后改写，不能只靠异常传递。"""
    task_id = get_current_task_run_id()
    if task_id is not None:
        try:
            await asyncio.wait_for(_write_note(int(task_id), reason, provider_task_id), _NOTE_TIMEOUT_SEC)
        except Exception:  # noqa: BLE001 — 标记失败仍要抛出，由异常链兜底，不能当普通失败重试
            logger.exception("视频待核对标记写入失败 task=%s reason=%s", task_id, reason)
    raise VideoBillingPending(reason, provider_task_id=provider_task_id) from cause


def video_cost_unconfirmed(task) -> bool:
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    if result.get("billing_review_resolved"):
        return False
    if result.get("video_billing_pending") or result.get("video_submission_pending"):
        return True
    video_type = task.task_type in {"fragment_video", "asset_video", "shot_regen_video", "tool_video", "v1_video", "v1_seedance"}
    # 取消（用户/作废）沿用线上规则：上游已受理即按预扣额计费（record_accepted_upstream_charge），
    # 不转待核对；只有失败且费用未知、或显式标记的提交/轮询未知才保留冻结待核对。
    return bool(video_type and task.provider_task_id and task.status == "failed"
                and not result.get("video_terminal_confirmed") and task.current_step_status != "done")


def video_deadline_exceeded(started_at, *, now=None) -> bool:
    from app.config import get_settings

    if started_at is None:
        return False
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - started_at).total_seconds() >= max(1, get_settings().video_task_total_timeout)


async def note_video_submission_started(body: dict) -> None:
    """已预扣的资产视频外调前留下可恢复标记。"""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models_tasks import TaskRun
    from app.services.billing.context import get_current_task_run_id

    task_id = get_current_task_run_id()
    if not task_id:
        return
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(TaskRun).where(TaskRun.id == task_id).with_for_update())).scalar_one()
        result = dict(task.result_payload or {})
        if result.get("video_submission_pending") and not task.provider_task_id:
            raise VideoBillingPending("interrupted_submission_unknown")
        task.result_payload = {**result, "video_submission_pending": True, "video_request": {
            "generate_audio": bool(body.get("generate_audio", False)), "model": str(body.get("model") or ""),
        }}
        await db.commit()


async def finish_current_video_task() -> None:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models_tasks import TaskRun
    from app.services.billing.context import get_current_task_run_id

    task_id = get_current_task_run_id()
    if not task_id:
        return
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(TaskRun).where(TaskRun.id == task_id).with_for_update())).scalar_one()
        task.result_payload = {k: v for k, v in (task.result_payload or {}).items()
                               if k not in {"video_submission_pending", "video_billing_pending"}}
        task.result_payload = {**task.result_payload, "video_terminal_confirmed": True}
        await db.commit()


def confirmed_video_cost_fen(raw_usage: dict | None, upstream_cost_fen: int | None = None) -> int | None:
    """终态费用证据；缺字段和默认 token=0 都不能视为免费。"""
    from math import isfinite
    from app.services.billing.pricing import parse_upstream_cost_fen

    def nonnegative(value):
        try:
            number = float(value)
            return number if not isinstance(value, bool) and isfinite(number) and number >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    if upstream_cost_fen is not None:
        value = nonnegative(upstream_cost_fen)
        return int(value) if value is not None and value.is_integer() else None
    raw = raw_usage if isinstance(raw_usage, dict) else {}
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else raw
    # 负值和非法值不因旧解析器的 max(0, value) 被解释成明确免费。
    for key in ("cost_fen", "cost_cents"):
        if key in usage:
            value = nonnegative(usage[key])
            return int(value) if value is not None and value.is_integer() else None
    for key in ("quota", "quota_consumed", "consumed_quota"):
        value = usage.get(key, raw.get(key))
        if value is not None:
            number = nonnegative(value)
            if number is None or not number.is_integer():
                return None
            from app.services.tokenfree_usage import quota_to_cost_fen
            return quota_to_cost_fen(int(number))
    parsed = parse_upstream_cost_fen({"usage": usage})
    if parsed == 0:
        return 0 if any(nonnegative(usage.get(key)) == 0 for key in (
            "cost_yuan", "total_cost_yuan", "cost", "total_cost", "amount",
        )) else None
    return parsed
