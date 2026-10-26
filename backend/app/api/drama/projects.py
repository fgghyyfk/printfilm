"""Drama project CRUD."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, false, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import UsageEvent, User
from app.models_drama import DramaAsset, DramaEpisode, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.schemas_common import PageMeta
from app.schemas_drama import (
    DramaProjectCreate,
    DramaProjectListItem,
    DramaProjectListOut,
    DramaProjectOut,
    DramaProjectUpdate,
    DramaProjectUsageStats,
    DramaScriptOut,
)
from app.services.drama.access import get_owned_drama_project, require_drama_project_manager
from app.services.teams.write_access import workspace_team
from app.services.teams.access import get_team
from app.services.drama.generation import project_link_last_frame_enabled
from app.services.drama.project_cover import resolve_drama_project_cover
from app.services.drama.usage_stats import (
    aggregate_drama_usage_by_project_ids,
    empty_usage_stats,
    get_drama_project_usage,
)
from app.services.drama.workflow import (
    CANVAS_SOURCE_MARKER,
    CANVAS_TITLE_MARKER,
    build_project_params,
    resolve_drama_workflow,
)
from app.services.tasks.service import cancel_tasks_for_scope, rebalance_project_fragment_video_queue

router = APIRouter()


async def _usage_actor(db: AsyncSession, team_id: int | None, user_id: int) -> int | None:
    """所有者看团队计费合计，其他成员只看本人；历史个人费用留在个人账单。"""
    if team_id is not None:
        from app.services.teams.access import member_role
        team, member = await get_team(db, team_id, user_id)
        if member_role(team, member) == "owner":
            return None
    return user_id


def _project_out(project: DramaProject, usage: DramaProjectUsageStats | None = None) -> DramaProjectOut:
    # 组装项目详情响应，附带用量统计
    script = None
    if project.script:
        script = DramaScriptOut.model_validate(project.script)
    return DramaProjectOut(
        id=project.id,
        user_id=project.user_id,
        team_id=project.team_id,
        title=project.title,
        description=project.description,
        content=project.content,
        params=project.params,
        created_at=project.created_at,
        updated_at=project.updated_at,
        script=script,
        asset_count=len(project.assets) if project.assets is not None else 0,
        episode_count=len(project.episodes) if project.episodes is not None else 0,
        workflow=resolve_drama_workflow(project),
        usage=usage or empty_usage_stats(),
    )


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，保证搜索词按字面匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _legacy_canvas_condition():
    """workflow 字段落地前的自由画布项目：靠标题或占位创意文案识别。"""
    return or_(
        DramaProject.title.like(f"%{CANVAS_TITLE_MARKER}%"),
        exists(
            select(DramaScript.id).where(
                DramaScript.project_id == DramaProject.id,
                DramaScript.source.like(f"%{CANVAS_SOURCE_MARKER}%"),
            )
        ),
    )


def _workflow_condition(workflow: str):
    """工作流判定的 SQL 表达，语义与 resolve_drama_workflow 保持一致。"""
    # 与 Python strip() 对齐：btrim 显式去掉空白字符，而非仅空格
    params_wf = func.lower(func.btrim(func.coalesce(DramaProject.params["workflow"].as_string(), ""), " \t\n\r\v\f"))
    declared = params_wf.in_(["canvas", "script"])
    legacy = _legacy_canvas_condition()
    if workflow == "canvas":
        return or_(params_wf == "canvas", and_(~declared, legacy))
    return or_(params_wf == "script", and_(~declared, ~legacy))


def _status_condition(status: str | None):
    """列表进度筛选的 SQL 表达，语义与原前端筛选保持一致。"""
    if not status or status == "all":
        return None
    episode_count = (
        select(func.count(DramaEpisode.id)).where(DramaEpisode.project_id == DramaProject.id).scalar_subquery()
    )
    asset_count = (
        select(func.count(DramaAsset.id)).where(DramaAsset.project_id == DramaProject.id).scalar_subquery()
    )
    has_script = exists(select(DramaScript.id).where(DramaScript.project_id == DramaProject.id))
    if status == "draft":
        canvas_cond = asset_count == 0
        script_cond = ~has_script
    elif status == "running":
        canvas_cond = asset_count > 0
        script_cond = and_(has_script, episode_count > 0)
    else:  # done：画布无完成态，漫剧以 8 集为完本线
        canvas_cond = false()
        script_cond = and_(has_script, episode_count >= 8)
    return or_(
        and_(_workflow_condition("canvas"), canvas_cond),
        and_(_workflow_condition("script"), script_cond),
    )


@router.get("/projects", response_model=DramaProjectListOut)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    scope_team_id: int | None = Depends(workspace_team),
    workflow: Literal["script", "canvas"] | None = None,
    status: Literal["all", "running", "done", "draft"] | None = Query(None, description="all|running|done|draft"),
    q: str | None = Query(None, description="标题搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(9, ge=1, le=200),
) -> DramaProjectListOut:
    # List current workspace's drama projects, paged；筛选与分页均在服务端完成
    team_id = scope_team_id if isinstance(scope_team_id, int) else None
    conditions = [
        DramaProject.team_id == team_id
        if team_id is not None
        else ((DramaProject.user_id == user.id) & DramaProject.team_id.is_(None))
    ]
    if workflow is not None:
        conditions.append(_workflow_condition(workflow))
    status_cond = _status_condition(status)
    if status_cond is not None:
        conditions.append(status_cond)
    needle = (q or "").strip()
    if needle:
        conditions.append(DramaProject.title.ilike(f"%{_escape_like(needle)}%", escape="\\"))

    total = (await db.execute(select(func.count()).select_from(DramaProject).where(*conditions))).scalar_one()
    page_count = max(1, -(-total // page_size))
    page = min(page, page_count)
    result = await db.execute(
        select(DramaProject)
        .where(*conditions)
        .options(
            selectinload(DramaProject.script),
            selectinload(DramaProject.assets),
            selectinload(DramaProject.episodes).selectinload(DramaEpisode.fragments),
        )
        .order_by(DramaProject.updated_at.desc(), DramaProject.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(result.scalars().all())
    usage_map = await aggregate_drama_usage_by_project_ids(
        db, user_id=await _usage_actor(db, team_id, user.id), project_ids=[p.id for p in rows], team_id=team_id
    )
    items: list[DramaProjectListItem] = []
    for p in rows:
        cover_url, cover_pending = resolve_drama_project_cover(p)
        items.append(
            DramaProjectListItem(
                id=p.id,
                team_id=p.team_id,
                title=p.title,
                description=p.description,
                created_at=p.created_at,
                updated_at=p.updated_at,
                episode_count=len(p.episodes or []),
                asset_count=len(p.assets or []),
                has_script=p.script is not None,
                cover_url=cover_url,
                cover_pending=cover_pending and not cover_url,
                workflow=resolve_drama_workflow(p),
                usage=usage_map.get(p.id) or empty_usage_stats(),
            )
        )
    return DramaProjectListOut(items=items, meta=PageMeta(page=page, page_size=page_size, total=total))


@router.post("/projects", response_model=DramaProjectOut)
async def create_project(
    body: DramaProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    scope_team_id: int | None = Depends(workspace_team),
) -> DramaProjectOut:
    # Create project + empty script draft from creative source
    source = (body.source or "").strip()
    if len(source) < 20:
        raise HTTPException(status_code=400, detail="原始创意至少需要 20 个字")
    title = (body.title or "").strip()
    if not title:
        title = source[:40] + ("…" if len(source) > 40 else "")
    workflow = (body.workflow or "script").strip().lower()
    if workflow not in ("script", "canvas"):
        workflow = "script"
    project_params = build_project_params(
        workflow=workflow,
        episode_count=body.episode_count,
        image_style_id=body.image_style_id,
        extra=body.params,
    )
    team_id = body.team_id or (scope_team_id if isinstance(scope_team_id, int) else None)
    if body.team_id and isinstance(scope_team_id, int) and body.team_id != scope_team_id:
        raise HTTPException(status_code=422, detail="项目团队与当前空间不匹配")
    if team_id is not None:
        await get_team(db, team_id, user.id, lock=True)
    project = DramaProject(
        user_id=user.id,
        team_id=team_id,
        title=title or ("自由画布项目" if workflow == "canvas" else "未命名漫剧"),
        description=body.description,
        params=project_params,
    )
    db.add(project)
    await db.flush()
    script = DramaScript(
        project_id=project.id,
        name=project.title,
        source=source,
        params={
            "episode_count": project_params.get("episode_count") or body.episode_count,
            "image_style_id": body.image_style_id,
            "summary_status": "skipped" if workflow == "canvas" else "pending",
            "episode_content_status": "skipped" if workflow == "canvas" else "pending",
            "workflow": workflow,
        },
    )
    db.add(script)
    if team_id is not None:
        from app.services.teams.activity import record_activity

        await record_activity(db, team_id, user.id, "创建了项目", project.title, project_id=project.id)
    await db.commit()
    project = await get_owned_drama_project(
        db, project.id, user, with_script=True, with_assets=True, with_episodes=True
    )
    return _project_out(project)


@router.get("/projects/{project_id}", response_model=DramaProjectOut)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaProjectOut:
    project = await get_owned_drama_project(
        db, project_id, user, with_script=True, with_assets=True, with_episodes=True
    )
    usage = await get_drama_project_usage(db, user_id=await _usage_actor(db, project.team_id, user.id), project_id=project.id, team_id=project.team_id)
    return _project_out(project, usage)


@router.patch("/projects/{project_id}", response_model=DramaProjectOut)
async def update_project(
    project_id: int,
    body: DramaProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaProjectOut:
    project = await get_owned_drama_project(db, project_id, user, with_script=True)
    prev_link = project_link_last_frame_enabled(project)
    if body.title is not None:
        project.title = body.title.strip() or project.title
    if body.description is not None:
        project.description = body.description
    if body.content is not None:
        project.content = body.content
    if body.params is not None:
        project.params = body.params
    await db.commit()

    # 镜间衔接开关变化时，动态重排仍排队的分镜视频任务
    next_link = project_link_last_frame_enabled(project)
    if body.params is not None and prev_link != next_link:
        await rebalance_project_fragment_video_queue(
            db,
            project_id,
            sequential=next_link,
            user_id=user.id,
        )

    project = await get_owned_drama_project(
        db, project_id, user, with_script=True, with_assets=True, with_episodes=True
    )
    usage = await get_drama_project_usage(db, user_id=await _usage_actor(db, project.team_id, user.id), project_id=project.id, team_id=project.team_id)
    return _project_out(project, usage)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """删除漫剧项目；先解绑任务/账单外键，避免 FK 阻塞 CASCADE。"""
    project = await get_owned_drama_project(db, project_id, user)
    await require_drama_project_manager(db, project, user.id)
    if project.team_id is not None:
        from app.services.teams.edit_locks import _task_conflicts

        await _task_conflicts(db, project.id, ("structure", 0))
    await cancel_tasks_for_scope(db, user.id, drama_project_id=project_id)

    # 保留任务与用量历史，仅清空指向本项目及子表的外键
    await db.execute(
        update(UsageEvent)
        .where(UsageEvent.drama_project_id == project_id)
        .values(drama_project_id=None)
    )
    await db.execute(
        update(TaskRun)
        .where(TaskRun.drama_project_id == project_id)
        .values(
            drama_project_id=None,
            script_id=None,
            episode_id=None,
            fragment_id=None,
            asset_id=None,
        )
    )
    # 兜底：drama_project_id 为空但仍挂在子表上的任务
    ep_ids = select(DramaEpisode.id).where(DramaEpisode.project_id == project_id)
    asset_ids = select(DramaAsset.id).where(DramaAsset.project_id == project_id)
    script_ids = select(DramaScript.id).where(DramaScript.project_id == project_id)
    await db.execute(
        update(TaskRun).where(TaskRun.episode_id.in_(ep_ids)).values(episode_id=None, fragment_id=None)
    )
    await db.execute(update(TaskRun).where(TaskRun.asset_id.in_(asset_ids)).values(asset_id=None))
    await db.execute(update(TaskRun).where(TaskRun.script_id.in_(script_ids)).values(script_id=None))

    await db.delete(project)
    await db.commit()
    return {"ok": True}
