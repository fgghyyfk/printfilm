"""带货分镜逐镜切换「动态 / 静图」与动作调整。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Project, ProjectStatus, Shot, User
from app.services.compliance.changes import clean_fields
from app.services.daihuo.motion import (
    DEFAULT_ACTION,
    MOTION_ACTIONS,
    build_video_prompt,
    is_daihuo,
    motion_unavailable_reason,
)
from app.services.kepu_stages import VIDEO_SKIP_REASON_STATIC, resolve_kepu_billing_phase

DETAIL_MAX = 60


async def set_shot_motion(db, user: User, project_id: int, shot_id: int, *, motion: bool,
                          action: str | None = None, detail: str | None = None) -> Shot:
    """切换单镜动态/静图并作废成片。

    静图口播项目首次打开某镜动态时整体转为混合展示（其余镜头标静图），已生成的配音保留。
    有进行中的任务时拒绝，避免与流水线并发改同一镜头。
    """
    from app.models_tasks import TaskRun
    from app.services.tasks.service import ACTIVE_TASK_STATUSES

    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
        .options(selectinload(Project.shots)).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if project is None or not is_daihuo(project):
        raise LookupError("项目不存在")
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if shot is None:
        raise LookupError("分镜不存在")
    active = await db.scalar(select(TaskRun.id).where(
        TaskRun.project_id == project_id, TaskRun.status.in_(ACTIVE_TASK_STATUSES)).limit(1))
    if active:
        raise ValueError("项目正在生成中，请等当前任务完成后再调整")
    if action is not None and action not in MOTION_ACTIONS:
        raise ValueError("不支持的镜头动作")

    if motion:
        if reason := motion_unavailable_reason():
            raise ValueError(reason)
        if (project.pipeline_mode or "") != "full":
            # 静图口播升级为混合展示：其余镜头保持静图推拉
            project.pipeline_mode = "full"
            project.daihuo_video_form = "mixed"
            for other in project.shots:
                if other.id != shot.id:
                    other.video_skip_reason = VIDEO_SKIP_REASON_STATIC
                    other.video_url = None
        key = action or _action_of(shot) or DEFAULT_ACTION
        text = (detail or "").strip()[:DETAIL_MAX]
        if text:
            text = clean_fields({"video_prompt": text})[0]["video_prompt"]
        new_prompt = build_video_prompt(key, text) if (action is not None or detail is not None or not shot.video_prompt) \
            else shot.video_prompt
        changed = shot.video_skip_reason == VIDEO_SKIP_REASON_STATIC or new_prompt != shot.video_prompt
        shot.video_prompt = new_prompt
        shot.camera = MOTION_ACTIONS[key][0]
        shot.video_skip_reason = None
        if changed:
            shot.video_url = None
            shot.last_frame_url = None
    else:
        if (project.pipeline_mode or "") != "full":
            return shot  # 静图口播本来就是静图
        shot.video_skip_reason = VIDEO_SKIP_REASON_STATIC
        shot.video_url = None
        shot.last_frame_url = None
    if (project.pipeline_mode or "") == "full":
        statics = sum(1 for s in project.shots if s.video_skip_reason == VIDEO_SKIP_REASON_STATIC)
        project.daihuo_video_form = "motion" if statics == 0 else "mixed"
    shot.version += 1
    _demote(project)
    await db.commit()
    await db.refresh(shot)
    return shot


def _action_of(shot: Shot) -> str | None:
    """由镜头的动作短名反查白名单 key。"""
    return next((k for k, v in MOTION_ACTIONS.items() if v[0] == (shot.camera or "")), None)


def _demote(project: Project) -> None:
    """作废成片并按素材进度把终态打回对应阶段（与分镜编辑一致）。"""
    project.final_video_url = None
    if project.status not in {ProjectStatus.DONE, ProjectStatus.AUDITING, ProjectStatus.COMPOSING,
                              ProjectStatus.VIDEO_READY, ProjectStatus.FAILED, ProjectStatus.CANCELLED}:
        return
    phase = resolve_kepu_billing_phase(project)
    if phase in {"script", "assets"}:
        project.status = ProjectStatus.SCRIPT_READY
    elif phase == "videos":
        project.status = ProjectStatus.IMAGE_READY
    else:
        project.status = ProjectStatus.VIDEO_READY
