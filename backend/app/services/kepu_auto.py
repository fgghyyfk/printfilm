"""科普逐阶段自动接续；成功结算与唯一下一阶段在同一事务提交。"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Project, ProjectStatus, UsageEvent, User
from app.models_tasks import TaskRun
from app.services.tasks.service import ACTIVE_TASK_STATUSES


def _materials(project: Project) -> str:
    data = [project.pipeline_mode, project.final_video_url, project.status, project.voice_id,
            project.image_model, project.video_model, project.resolution_mode,
            [(s.id, s.version, s.image_url, s.image_ark_url, s.video_url, s.audio_url,
              s.video_skip_reason, s.narration, s.duration) for s in project.shots]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


async def stop_auto_chain(db, project_id: int, *, chain_id: str | None = None) -> bool:
    """所有取消先锁项目再写任务，覆盖阶段切换刚创建的子任务。"""
    with db.no_autoflush:
        project = (await db.execute(select(Project).where(Project.id == project_id).with_for_update()
                   .execution_options(populate_existing=True))).scalar_one_or_none()
    if not project or not project.auto_chain_id or (chain_id and project.auto_chain_id != chain_id):
        return False
    project.auto_chain_id = None
    project.status = ProjectStatus.CANCELLED
    project.error_msg = "用户取消自动生成"
    return True


async def cancel_auto_chain_for_task(db, task: TaskRun) -> bool:
    chain = (task.payload or {}).get("auto_chain_id")
    if task.domain != "kepu" or not task.project_id or not chain:
        return False
    if not await stop_auto_chain(db, task.project_id, chain_id=chain):
        return False
    from datetime import UTC, datetime

    rows = list((await db.scalars(select(TaskRun).where(
        TaskRun.project_id == task.project_id, TaskRun.status.in_(tuple(ACTIVE_TASK_STATUSES)),
    ).with_for_update().execution_options(populate_existing=True))).all())
    for row in rows:
        if (row.payload or {}).get("auto_chain_id") == chain:
            row.cancel_requested = True
            row.status = "cancel_requested"
            row.next_action_at = datetime.now(UTC)
    return True


async def complete_auto_stage(db, task: TaskRun, result: dict) -> None:
    from app.schemas_tasks import TaskCreateRequest, TaskTargetBind
    from app.services.billing.estimates import estimate_phase_fen, is_free_local_task
    from app.services.billing.newapi_bill import prepare_newapi_bill_logs
    from app.services.billing.settlement import PreparedTaskSettlements, finish_prepared_task_settlements, billing_active
    from app.services.kepu_stages import resolve_kepu_billing_phase
    from app.services.public_errors import InsufficientBalanceError
    from app.services.tasks.executor import _complete_task
    from app.services.tasks.service import create_task
    from app.services.tokenfree_pricing import ensure_official_rates

    chain = (task.payload or {}).get("auto_chain_id")
    # 重复收尾不能再推进或改写已终态父任务。
    if task.status in {"succeeded", "failed", "cancelled", "awaiting_review"}:
        return
    project = (await db.execute(select(Project).where(Project.id == task.project_id)
               .options(selectinload(Project.shots), selectinload(Project.template)))).scalar_one_or_none()
    if not project:
        return
    signature = _materials(project)
    finished = project.status == ProjectStatus.DONE and bool(project.final_video_url)
    next_phase = None if finished else resolve_kepu_billing_phase(project)
    # 价目、音频检查、上游账单全部在项目/任务/钱包锁外准备。
    estimate = 0
    if next_phase and billing_active():
        await ensure_official_rates()
        estimate = estimate_phase_fen(project, next_phase)
    events = list((await db.scalars(select(UsageEvent).where(
        UsageEvent.task_run_id == task.id, UsageEvent.settled.is_(False),
    ))).all())
    prepared = PreparedTaskSettlements(event_ids={task.id: {e.id for e in events}},
                                       newapi_logs={task.id: await prepare_newapi_bill_logs(events)})
    with db.no_autoflush:
        project = (await db.execute(select(Project).where(Project.id == task.project_id).with_for_update()
                   .options(selectinload(Project.shots), selectinload(Project.template))
                   .execution_options(populate_existing=True))).scalar_one_or_none()
        task = (await db.execute(select(TaskRun).where(TaskRun.id == task.id).with_for_update()
                .options(selectinload(TaskRun.steps)).execution_options(populate_existing=True))).scalar_one()
    if task.status in {"succeeded", "failed", "cancelled", "awaiting_review"}:
        return
    await _complete_task(db, task, result, commit=False, prepared=prepared, auto_handled=True)
    reason = None
    authorized = bool(project and project.auto_chain_id == chain and project.auto_advance)
    if not authorized or task.status != "succeeded" or task.billing_status != "settled":
        reason = "自动生成已停止或当前阶段费用待核对"
    elif _materials(project) != signature:
        reason = "素材已更新，自动生成已暂停，请确认后继续"
    elif finished:
        project.auto_chain_id = None
        task.result_payload = {**task.result_payload, "auto_finished": True}
    elif next_phase == (task.payload or {}).get("phase"):
        reason = "当前阶段尚未就绪，自动生成已暂停，请确认素材后继续"
    else:
        user = await db.get(User, task.requested_by)
        try:
            async with db.begin_nested():
                # 钱包已由父结算锁住；旧的无报价排队任务不可在锁内触发冷价目请求。
                pending = list((await db.scalars(select(TaskRun).where(
                    TaskRun.requested_by == user.id, TaskRun.billing_status == "none",
                    TaskRun.status.in_(tuple(ACTIVE_TASK_STATUSES)), TaskRun.billing_estimate_fen <= 0,
                ))).all()) if billing_active(user) else []
                if any(not is_free_local_task(row) for row in pending):
                    raise ValueError("排队任务报价尚未确认")
                child = await create_task(db, user, TaskCreateRequest(
                    domain="kepu", task_type="project_compose_only" if next_phase == "compose" else "project_pipeline",
                    project_id=project.id, payload={"project_id": project.id, "phase": next_phase,
                                                   "auto_chain_id": chain, "auto_parent_task_id": task.id},
                    dedupe_key=f"kepu:auto:{task.id}", targets=[TaskTargetBind(target_type="project", target_id=project.id)],
                ), commit=False, prepared_estimate_fen=estimate)
            task.result_payload = {**task.result_payload, "next_task_id": child.id}
            project.status = {"script": ProjectStatus.SCRIPTING, "assets": ProjectStatus.IMAGING,
                              "videos": ProjectStatus.VIDEOING, "compose": ProjectStatus.COMPOSING}[next_phase]
            project.error_msg = None
        except InsufficientBalanceError:
            reason = "余额不足，自动生成已暂停；本阶段费用已结算，已有素材已保留"
        except ValueError:
            reason = "下一阶段暂无法入队，自动生成已暂停；已有素材和本阶段结算已保留"
    if reason:
        if project and project.auto_chain_id == chain:
            project.auto_chain_id = None
            project.error_msg = reason
        task.result_payload = {**task.result_payload, "auto_paused": reason}
    await db.commit()
    await finish_prepared_task_settlements(db, prepared)
