"""视频提交/轮询结果未知时保留原任务和冻结，禁止把超时当免费失败。"""
from __future__ import annotations

from datetime import UTC, datetime


class VideoBillingPending(RuntimeError):
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


def mark_video_billing_pending(task, exc: BaseException) -> bool:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, VideoBillingPending):
            result = dict(task.result_payload or {})
            result["video_billing_pending"] = {"reason": current.reason[:100], "provider_task_id": current.provider_task_id}
            task.result_payload = result
            if current.provider_task_id:
                task.provider_task_id = current.provider_task_id
            return True
        current = current.__cause__ or current.__context__
    return False


def video_cost_unconfirmed(task) -> bool:
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    if result.get("billing_review_resolved"):
        return False
    if result.get("video_billing_pending") or result.get("video_submission_pending"):
        return True
    video_type = task.task_type in {"fragment_video", "asset_video", "shot_regen_video", "tool_video", "v1_video", "v1_seedance"}
    return bool(video_type and task.provider_task_id and task.status in {"failed", "cancelled", "cancel_requested"}
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
