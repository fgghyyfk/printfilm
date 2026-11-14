"""调度扫描与执行器中断共用的短事务回收边界。"""
from datetime import UTC, datetime

from sqlalchemy import JSON, and_, cast, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services.tasks.service import append_task_event, set_task_step_state, task_finalizing_window_open

# 这些任务重新进入后复用已有上游任务号，不会再次提交付费请求。
_PROVIDER_RESUMABLE = frozenset({
    ("drama", "fragment_video"), ("drama", "asset_video"), ("studio", "tool_video"),
})


def _provider_resumable(task, result: dict) -> bool:
    """已有上游任务号时能否安全重入：固定名单，或科普单镜重生成已记录逐镜上游任务。"""
    if (task.domain, task.task_type) in _PROVIDER_RESUMABLE:
        return True
    return task.domain == "kepu" and task.task_type == "shot_regen_video" and bool(result.get("video_tasks"))


async def recover_interrupted_task(db, task_id: int, *, stale_clause=None, job_alive=None,
                                   event_type: str = "task.requeued", count_recovery: bool = True) -> bool:
    """逐任务锁内重核后恢复；第三次中断失败，已提交费用通过标准失败路径结算。

    count_recovery=False 用于计划内停机（如热更新），不消耗累计恢复次数。
    """
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
    if isinstance(task.result_payload, dict) and task.result_payload.get("voice_billing_reservation"):
        from app.services.billing.ephemeral import durable_voice_request_alive

        if durable_voice_request_alive(task_id):
            return False
        # 真正消失的音色 HTTP 请求不可重排 noop 或按零成本解冻。
        task.result_payload = {**task.result_payload, "tts_billing_pending": {
            "reason": "voice_request_interrupted", "cost_unknown": True,
        }}
        task.status = "awaiting_review"
        task.error_code = "tts_billing_pending"
        task.error_message = "音色合成请求中断，结果和费用待核对，请勿重新生成"
        task.current_step_status = "awaiting_review"
        task.progress_percent = min(int(task.progress_percent or 0), 99)
        task.finished_at = None
        task.next_action_at = None
        task.lease_token = None
        task.lease_until = None
        await append_task_event(db, task.id, event_type="task.awaiting_review",
                                status=task.status, message=task.error_message)
        await db.commit()
        return True
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
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    if result.get("video_billing_pending") or result.get("video_submission_pending"):
        # 视频提交/结果未知：仅已有可复用上游任务号的类型继续恢复，其余直接转待核对，不能重提
        rows = list((result.get("video_tasks") or {}).values())
        recoverable = ((task.domain == "kepu" and rows and all(
            row.get("state") == "done" or row.get("provider_task_id") for row in rows))
            or (task.domain == "drama" and task.task_type in {"fragment_video", "asset_video"} and task.provider_task_id))
        if not recoverable or task.cancel_requested:
            from app.services.billing.settlement import settle_task

            await settle_task(db, task.id)
            await db.commit()
            return True
    if task.cancel_requested or task.status == "cancel_requested":
        await _mark_cancelled(db, task)
        return True

    # 两入口共享累计次数；兼容旧表中曾写入的 NULL。
    task.recovery_count = int(task.recovery_count or 0) + int(count_recovery)
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
    if count_recovery and task.recovery_count >= limit:
        if provider_id:
            _mark_provider_cost_unknown(task, provider_id)
        await _fail_recovered_task(db, task, f"任务执行中断已达 {limit} 次，已停止重试，按实际用量结算并退回剩余冻结金额")
        return True
    if provider_id and not _provider_resumable(task, result):
        _mark_provider_cost_unknown(task, provider_id)
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


def _mark_provider_cost_unknown(task, provider_id: str) -> None:
    """上游已接受的任务放弃跟进后费用未知，保留冻结待核对，不能全额退款。"""
    from app.services.video_recovery import VIDEO_PENDING_KEY

    task.result_payload = {
        **(task.result_payload if isinstance(task.result_payload, dict) else {}),
        VIDEO_PENDING_KEY: {"reason": "interrupted_after_accept", "provider_task_id": provider_id},
    }


async def _fail_recovered_task(db, task, message: str) -> None:
    """走标准失败结算；分镜规划任务的分集展示由 _fail_task 一并收敛。"""
    from app.services.tasks.executor import _fail_task

    await _fail_task(db, task, RuntimeError(message))


def fragment_plan_target(task) -> tuple[int, int] | None:
    """分镜规划任务返回 (任务号, 分集号)；结算会卸载关系，须在结算前取快照。"""
    if task.domain == "drama" and task.task_type == "fragment_plan" and task.episode_id:
        return int(task.id), int(task.episode_id)
    return None


async def converge_fragment_plan_status(db, task_id: int, episode_id: int, message: str) -> None:
    """任务结束但分集仍显示分镜中时转为失败；须在任务结算提交、释放任务锁后调用。

    带 expected_revision 的新任务按唯一任务号归属（同 revision 的旧 force 任务不能覆盖当前任务）；
    升级前无 owner 的旧任务仅在没有更新的规划任务时收敛。
    """
    from app.models_drama import DramaEpisode

    expected = (await db.execute(select(TaskRun.payload).where(TaskRun.id == task_id))).scalar_one_or_none()
    expected = expected.get("expected_revision") if isinstance(expected, dict) else None
    params = cast(DramaEpisode.params, JSONB)
    owner = DramaEpisode.params["fragment_plan_task_id"].as_string()
    # 强制重新规划可能已建了新任务；旧任务到限不能覆盖新任务的 generating。
    newer = exists(select(TaskRun.id).where(
        TaskRun.domain == "drama", TaskRun.task_type == "fragment_plan",
        TaskRun.episode_id == episode_id, TaskRun.id > task_id,
    ))
    conditions = [
        DramaEpisode.id == episode_id,
        DramaEpisode.params["fragment_plan_status"].as_string() == "generating",
        or_(owner == str(task_id), and_(owner.is_(None), ~newer)),
    ]
    if type(expected) is int:
        conditions += [
            DramaEpisode.revision == expected,
            DramaEpisode.params["fragment_plan_revision"].as_integer() == expected,
        ]
    patch = {"fragment_plan_status": "failed", "fragment_plan_error": message[:500]}
    await db.execute(update(DramaEpisode).where(*conditions)
                     .values(params=cast(func.coalesce(params, cast({}, JSONB)).op("||")(cast(patch, JSONB)), JSON))
                     .execution_options(synchronize_session=False))
    await db.commit()
