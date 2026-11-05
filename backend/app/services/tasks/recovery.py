"""调度扫描与执行器中断共用的短事务回收边界。"""
from datetime import UTC, datetime

from sqlalchemy import JSON, cast, exists, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services.tasks.service import append_task_event, set_task_step_state, task_finalizing_window_open

# 这些任务重新进入后复用已有上游任务号，不会再次提交付费请求。
_PROVIDER_RESUMABLE = frozenset({
    ("drama", "fragment_video"), ("drama", "asset_video"), ("studio", "tool_video"),
})


async def recover_interrupted_task(db, task_id: int, *, stale_clause=None, job_alive=None,
                                   event_type: str = "task.requeued") -> bool:
    """逐任务锁内重核后恢复；第三次中断失败，已提交费用通过标准失败路径结算。"""
    from app.services.tasks.executor import _mark_cancelled

    # 锁重读同时载入步骤，避免身份映射旧快照及异步懒加载。
    stmt = (select(TaskRun).where(TaskRun.id == task_id, TaskRun.status.in_(("leased", "running", "cancel_requested")))
            .options(selectinload(TaskRun.steps)).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True))
    if stale_clause is not None:
        stmt = stmt.where(stale_clause)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if task is None or (job_alive is not None and job_alive(task_id)):
        return False
    if task_finalizing_window_open(task):
        return False
    from app.services.tasks.inline import INLINE_LEASE_PREFIX

    if str(task.lease_token or "").startswith(INLINE_LEASE_PREFIX):
        # HTTP 内联任务没有可重放的作业：重启后不能重排 noop，也不能重发 POST。
        from app.services.billing.settlement import settle_task

        task.status = "cancelled" if task.cancel_requested else "failed"
        task.error_code = "inline_execution_interrupted"
        task.error_message = "生成请求执行中断，请重新发起"
        task.finished_at = datetime.now(UTC)
        task.next_action_at = None
        task.lease_token = None
        task.lease_until = None
        await append_task_event(db, task.id, event_type=f"task.{task.status}", status=task.status,
                                phase=task.current_step_key, message=task.error_message)
        if task.billing_status in {"frozen", "reserved"}:
            await settle_task(db, task.id)
        await db.commit()
        return True
    if task.cancel_requested or task.status == "cancel_requested":
        await _mark_cancelled(db, task)
        return True

    # 两入口共享累计次数；兼容旧表中曾写入的 NULL。
    task.recovery_count = int(task.recovery_count or 0) + 1
    limit = max(1, int(get_settings().task_runtime_max_recoveries))
    payload = dict(task.payload or {}) if isinstance(task.payload, dict) else {}
    provider_id = str(task.provider_task_id or payload.get("provider_task_id") or "").strip()
    if provider_id:
        payload["provider_task_id"] = provider_id
        task.payload = payload
        task.provider_task_id = provider_id
    task.lease_token = None
    task.lease_until = None
    task.next_action_at = None
    if task.recovery_count >= limit:
        await _fail_recovered_task(db, task, f"任务执行中断已达 {limit} 次，已停止重试，按实际用量结算并退回剩余冻结金额")
        return True
    if provider_id and (task.domain, task.task_type) not in _PROVIDER_RESUMABLE:
        await _fail_recovered_task(db, task, "任务执行中断，已有上游任务暂不支持安全恢复，已保留上游任务号，请联系客服核对")
        return True
    now = datetime.now(UTC)
    task.status = "awaiting_poll" if provider_id and task.task_type == "fragment_video" else "pending"
    step = task.steps[0] if task.steps else None
    set_task_step_state(task, step, status="polling" if task.status == "awaiting_poll" else "pending", now=now)
    task.next_action_at = now
    await append_task_event(db, task.id, event_type=event_type, status=task.status,
                            phase=task.current_step_key, message="检测到任务执行中断，已安排恢复",
                            payload={"recovery_count": task.recovery_count})
    await db.commit()
    return True


async def _fail_recovered_task(db, task, message: str) -> None:
    """先提交标准失败结算释放任务锁，再按当前规划归属收敛分集展示。"""
    from app.models_drama import DramaEpisode
    from app.services.tasks.executor import _fail_task

    # 结算会刷新任务并卸载关系；提前保留本次需要的标量快照。
    task_id, episode_id = int(task.id), task.episode_id
    is_plan = task.domain == "drama" and task.task_type == "fragment_plan"
    await _fail_task(db, task, RuntimeError(message))
    if not is_plan or not episode_id:
        return
    params = cast(DramaEpisode.params, JSONB)
    # 强制重新规划可能已建了新任务；旧任务到限不能覆盖新任务的 generating。
    newer = exists(select(TaskRun.id).where(
        TaskRun.domain == "drama", TaskRun.task_type == "fragment_plan",
        TaskRun.episode_id == episode_id, TaskRun.id > task_id,
    ))
    patch = {"fragment_plan_status": "failed", "fragment_plan_error": message[:500]}
    await db.execute(update(DramaEpisode).where(
        DramaEpisode.id == episode_id,
        DramaEpisode.params["fragment_plan_status"].as_string() == "generating",
        ~newer,
    ).values(params=cast(func.coalesce(params, cast({}, JSONB)).op("||")(cast(patch, JSONB)), JSON))
    .execution_options(synchronize_session=False))
    await db.commit()
