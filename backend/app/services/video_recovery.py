"""视频提交或轮询结果未知时保留冻结待核对，禁止把超时当免费失败全额退款。"""
from __future__ import annotations

import asyncio
import logging

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
