"""任务取消时的上游费用：上游已受理的任务停不下来，平台照付，不能全额退给用户。"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import UsageEvent
from app.models_tasks import TaskRun
from app.services.billing.pricing import billing_key_to_capability

CANCEL_CHARGE_MESSAGE = "上游已开始生成，取消后本次费用不退"
_FROZEN_STATUSES = {"frozen", "reserved"}


async def record_accepted_upstream_charge(db, task: TaskRun) -> bool:
    """上游已受理且未产生用量时，按预扣额记一行用量，随后的结算据此收费；返回是否记录。"""
    if not getattr(task, "provider_task_id", None) or str(task.billing_status or "") not in _FROZEN_STATUSES:
        return False
    recorded = int(await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.charge_fen), 0))
        .where(UsageEvent.task_run_id == task.id, UsageEvent.settled.is_(False))
    ) or 0)
    charge = max(0, int(task.billing_estimate_fen or 0) - recorded)
    if charge <= 0:
        return False
    db.add(UsageEvent(
        user_id=task.requested_by, task_run_id=task.id, cost_fen=charge, charge_fen=charge,
        billing_key="cancel_after_accept", domain=task.domain, provider="cancel_after_accept", model="",
        project_id=task.project_id, drama_project_id=task.drama_project_id, shot_id=task.shot_id,
        capability=billing_key_to_capability("cancel_after_accept"), estimated=True, settled=False,
        prompt_tokens=0, completion_tokens=0, total_tokens=0,
        raw_usage_json=json.dumps(
            {"simulated": True, "source": "cancel_after_accept", "provider_task_id": task.provider_task_id},
            ensure_ascii=False,
        ),
        created_at=datetime.now(UTC),
    ))
    await db.flush()
    return True
