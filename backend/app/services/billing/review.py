"""管理员核对待核对任务的费用，是 awaiting_review 唯一的结算出口。"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent
from app.models_tasks import TaskRun, TaskStep
from app.services.billing.pricing import billing_key_to_capability
from app.services.billing.settlement import _lock_task, settle_task
from app.services.tasks.service import append_task_event
from app.services.video_recovery import VIDEO_PENDING_KEY

logger = logging.getLogger(__name__)

_PENDING_KEYS = (VIDEO_PENDING_KEY, "tts_billing_pending")
_RESOLVED_CODE = "billing_review_resolved"
_RESOLVED_MESSAGE = "费用已核对，任务已结束；未使用的预扣已退回"


async def resolve_billing_review(
    db: AsyncSession, task_id: int, *, admin_id: int, cost_fen: int, charge_fen: int, note: str,
) -> TaskRun:
    """记录核定的上游成本和用户收费后结算，退回其余冻结金额。

    cost_fen 只进平台账；charge_fen 才向用户收取，与已记录未结算用量合计不得超过预扣额。
    核对依据存在管理员可见的用量行里，用户可见的任务事件不带这些内容。
    """
    from app.services.tasks import scheduler

    task = await _lock_task(db, task_id)
    if task is None:
        raise LookupError("任务不存在")
    if task.status != "awaiting_review":
        raise ValueError("只能处理待核对状态的任务")
    if scheduler._job_alive(int(task_id)):
        raise ValueError("任务仍在本地执行，请等待其退出后再核对")
    # 结算按全部未结算用量行求和，已记录的用量也占预扣额，否则超出部分会继续扣钱包。
    recorded = int(await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.charge_fen), 0))
        .where(UsageEvent.task_run_id == task.id, UsageEvent.settled.is_(False))
    ) or 0)
    if charge_fen > max(0, int(task.billing_estimate_fen or 0) - recorded):
        raise ValueError("本次收费加已记录用量不能超过本任务的预扣额")

    now = datetime.now(UTC)
    # 不论金额是否为零都留一行，保证每次核对都有据可查。
    db.add(UsageEvent(
        user_id=task.requested_by, task_run_id=task.id, cost_fen=cost_fen, charge_fen=charge_fen,
        billing_key="manual_review", domain=task.domain, provider="manual_review", model="",
        project_id=task.project_id, drama_project_id=task.drama_project_id, shot_id=task.shot_id,
        capability=billing_key_to_capability("manual_review"), estimated=False, settled=False,
        prompt_tokens=0, completion_tokens=0, total_tokens=0,
        raw_usage_json=json.dumps(
            {"simulated": True, "source": "manual_review", "admin_id": admin_id, "note": note,
             "pending": {key: task.result_payload.get(key) for key in _PENDING_KEYS
                         if isinstance(task.result_payload, dict) and key in task.result_payload}},
            ensure_ascii=False,
        ),
        created_at=now,
    ))
    await db.flush()

    task.result_payload = {
        **{k: v for k, v in (task.result_payload or {}).items() if k not in _PENDING_KEYS},
        _RESOLVED_CODE: True,
    }
    task.status = "failed"
    task.current_step_status = "failed"
    task.error_code = _RESOLVED_CODE
    task.error_message = _RESOLVED_MESSAGE
    task.finished_at = now
    task.next_action_at = None
    task.lease_token = None
    task.lease_until = None
    await db.execute(
        update(TaskStep)
        .where(TaskStep.task_id == task_id, TaskStep.status.not_in(("done", "failed", "cancelled")))
        .values(status="failed", finished_at=now, next_poll_at=None,
                error_code=_RESOLVED_CODE, error_message=_RESOLVED_MESSAGE)
    )
    await append_task_event(db, task_id, event_type="task.billing_review_resolved",
                            status=task.status, message=_RESOLVED_MESSAGE)
    await settle_task(db, task_id)
    await db.commit()
    logger.info("任务费用已核对 task=%s admin=%s cost_fen=%s charge_fen=%s",
                task_id, admin_id, cost_fen, charge_fen)
    return task
