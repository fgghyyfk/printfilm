"""视频提交/轮询结果未知时保留原任务和冻结，禁止把超时当免费失败。"""
from __future__ import annotations

from datetime import UTC, datetime


class VideoBillingPending(RuntimeError):
    def __init__(self, reason: str, *, provider_task_id: str | None = None):
        super().__init__("视频结果和费用待核对，已保留原任务，请勿重新生成")
        self.reason = reason
        self.provider_task_id = provider_task_id


class VideoGenerationFailed(RuntimeError):
    def __init__(self, result):
        super().__init__(result.error or "视频生成失败")
        self.result = result


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


async def note_video_submission_started() -> None:
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
        task.result_payload = {**result, "video_submission_pending": True}
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
