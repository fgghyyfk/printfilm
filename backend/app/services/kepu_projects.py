"""科普项目列表：统一筛选范围、分页统计及任务运行态投影。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus, Work
from app.schemas import PageMeta, ProjectListItem, ProjectListOut, ProjectListStats
from app.services.tasks.service import list_active_tasks_for_projects

# 列表“生成中”统计和无 phase 任务投影共用的项目状态集合。
_RUNNING_STATUSES = {
    ProjectStatus.SCRIPTING,
    ProjectStatus.IMAGING,
    ProjectStatus.VIDEOING,
    ProjectStatus.AUDIOING,
    ProjectStatus.COMPOSING,
    ProjectStatus.AUDITING,
}


def project_runtime_view(project: Project) -> tuple[str, int, str | None]:
    """从最新活动任务投影项目运行态，不修改项目持久化字段。"""
    active_tasks = list(getattr(project, "active_tasks", []) or [])
    if not active_tasks:
        return str(project.status), int(project.progress or 0), project.error_msg
    task = active_tasks[0]
    task_type = str(getattr(task, "task_type", "") or "")
    status = str(getattr(task, "status", "") or "")
    progress = int(getattr(task, "progress_percent", 0) or 0)
    if task_type == "project_pipeline":
        # 勿一律标 SCRIPTING：续跑 videos/assets 时列表会误显示「拆分镜中」
        payload = getattr(task, "payload", None) or {}
        phase = str(payload.get("phase") or "").strip().lower() if isinstance(payload, dict) else ""
        phase_label = {
            "script": ProjectStatus.SCRIPTING,
            "assets": ProjectStatus.IMAGING,
            "videos": ProjectStatus.VIDEOING,
            "compose": ProjectStatus.COMPOSING,
        }.get(phase)
        proj_status = str(project.status or "")
        if phase_label:
            label = phase_label
        elif proj_status in _RUNNING_STATUSES:
            label = proj_status
        else:
            label = ProjectStatus.SCRIPTING
    elif task_type == "shot_regen_image":
        label = ProjectStatus.IMAGING
    elif task_type == "shot_regen_video":
        label = ProjectStatus.VIDEOING
    elif task_type == "shot_regen_audio":
        label = ProjectStatus.AUDIOING
    elif task_type == "project_regen_audio":
        label = ProjectStatus.AUDIOING
    elif task_type == "project_compose_only":
        label = ProjectStatus.COMPOSING
    else:
        label = str(project.status)
    if status in {"failed", "cancelled"}:
        return status.upper(), progress, getattr(task, "error_message", None) or project.error_msg
    if status in {"pending", "leased", "running", "awaiting_poll", "cancel_requested"}:
        return label, max(progress, int(project.progress or 0)), getattr(task, "error_message", None)
    return str(project.status), int(project.progress or 0), project.error_msg


# 关键词和类型筛选同时用于列表、总数与统计，避免三个查询范围漂移。
def _scope_filters(user_id: int, mode: str, keyword: str) -> list:
    filters = [Project.user_id == user_id]
    if mode in {"full", "image_text"}:
        filters.append(Project.pipeline_mode == mode)
    if keyword:
        like = f"%{keyword}%"
        filters.append(or_(Project.title.ilike(like), Project.error_msg.ilike(like)))
    return filters


# 每个项目仅计算一次运行态，再组装列表响应。
def _list_item(project: Project, *, published: bool) -> ProjectListItem:
    status, progress, error_msg = project_runtime_view(project)
    return ProjectListItem(
        id=project.id,
        title=project.title,
        template_id=project.template_id,
        status=status,
        progress=progress,
        cover_url=project.cover_url,
        final_video_url=project.final_video_url,
        error_msg=error_msg,
        pipeline_mode=project.pipeline_mode or "full",
        output_ratio=project.output_ratio or "",
        published=published,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_tasks=list(getattr(project, "active_tasks", []) or []),
    )


async def list_project_page(
    db: AsyncSession,
    user_id: int,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    q: str | None = None,
    pipeline_mode: str | None = None,
) -> ProjectListOut:
    """分页读取用户项目，统计保留类型和关键词范围，但不受状态 Tab 限制。"""
    # published_exists 复用列表与总数的发布状态条件；scope 是统计的完整范围。
    published_exists = (
        select(Work.id)
        .where(Work.project_id == Project.id, Work.user_id == user_id)
        .correlate(Project)
        .exists()
    )
    mode = (pipeline_mode or "").strip().lower()
    keyword = (q or "").strip()
    scope = _scope_filters(user_id, mode, keyword)
    filters = list(scope)
    tab = (status or "all").strip().lower()
    if tab == "draft":
        filters.append(Project.status == ProjectStatus.DRAFT)
    elif tab == "running":
        filters.append(Project.status.in_(_RUNNING_STATUSES))
    elif tab == "done":
        filters.append(Project.status == ProjectStatus.DONE)
    elif tab == "published":
        filters.append(published_exists)

    # 只为当前页批量读取发布标记和任务，避免随页大小增加逐项目查询。
    count = select(func.count()).select_from(Project).where(*filters)
    total = int((await db.execute(count)).scalar_one() or 0)
    statement = (
        select(Project).where(*filters).order_by(Project.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    rows = list((await db.execute(statement)).scalars().all())
    published_ids: set[int] = set()
    if rows:
        project_ids = [project.id for project in rows]
        published = await db.execute(
            select(Work.project_id).where(Work.user_id == user_id, Work.project_id.in_(project_ids))
        )
        published_ids = {int(project_id) for project_id in published.scalars().all()}
        active_tasks = await list_active_tasks_for_projects(db, user_id, project_ids)
        for project in rows:
            project.active_tasks = active_tasks[int(project.id)]
    items = [_list_item(project, published=project.id in published_ids) for project in rows]

    # 按共同范围统计各状态；单会话查询保持串行，避免并发使用 AsyncSession。
    async def count_projects(*extra) -> int:
        """对当前类型与关键词范围追加状态条件后计数。"""
        statement = select(func.count()).select_from(Project).where(*scope, *extra)
        return int((await db.execute(statement)).scalar_one() or 0)

    stats_total = await count_projects()
    stats_generating = await count_projects(Project.status.in_(_RUNNING_STATUSES))
    stats_done = await count_projects(Project.status == ProjectStatus.DONE)
    published_count = (
        select(func.count()).select_from(Work).join(Project, Project.id == Work.project_id)
        .where(Work.user_id == user_id, *scope)
    )
    stats_published = int((await db.execute(published_count)).scalar_one() or 0)
    return ProjectListOut(
        items=items,
        meta=PageMeta(page=page, page_size=page_size, total=total),
        stats=ProjectListStats(
            total=stats_total, generating=stats_generating, done=stats_done, published=stats_published,
        ),
    )
