"""将现有漫剧写入口统一映射到实际团队资源，防止漏掉旧路由授权。"""
from __future__ import annotations

import re

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject
from app.services.drama.access import get_owned_drama_project, require_drama_project_manager
from app.services.teams.access import get_team
from app.services.teams.edit_locks import _task_conflicts, episode_resource_id, validate_lock


async def workspace_team(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> int | None:
    """仅列表/创建读取显式空间；资源授权始终根据数据库真实归属。"""
    query = request.query_params.get("team_id")
    header = request.headers.get("X-Team-Id")
    if not query and not header:
        return None
    try:
        team_id = int(query or header or "0")
        if team_id < 1 or (query and header and int(query) != int(header)):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="无效的团队空间") from exc
    await get_team(db, team_id, user.id)
    return team_id


async def write_target(db: AsyncSession, path: str, values: dict) -> tuple[int, str, int] | None:
    """推导路径和请求字段对应的资源键，不接受客户端指定锁范围。"""
    suffix = path.removeprefix("/api/drama/")
    match = re.match(r"scripts/(\d+)/episodes/(\d+)$", suffix)
    if match:
        return int(match[1]), "episode", int(match[2])
    match = re.match(r"(projects|scripts|canvas)/(\d+)$", suffix)
    if match:
        return int(match[2]), {"projects": "structure", "scripts": "script", "canvas": "canvas"}[match[1]], 0
    match = re.match(r"assets/(\d+)(?:/.*)?$", suffix)
    if match:
        asset = await db.get(DramaAsset, int(match[1]))
        if asset:
            return asset.project_id, "asset", asset.id
        raise HTTPException(status_code=404, detail="资产不存在")
    match = re.match(r"episodes/(\d+)(?:/.*)?$", suffix)
    if match:
        episode = await db.get(DramaEpisode, int(match[1]))
        if episode:
            return episode.project_id, "episode", await episode_resource_id(db, episode.id)
        raise HTTPException(status_code=404, detail="分集不存在")
    match = re.match(r"fragments/(\d+)(?:/.*)?$", suffix)
    if match:
        fragment = await db.get(DramaEpisodeFragment, int(match[1]))
        episode = await db.get(DramaEpisode, fragment.episode_id) if fragment else None
        if episode:
            return episode.project_id, "episode", await episode_resource_id(db, episode.id)
        raise HTTPException(status_code=404, detail="分镜不存在")
    project_id = values.get("project_id")
    if not project_id:
        return None
    if suffix == "canvas":
        return int(project_id), "canvas", 0
    if suffix == "assets":
        return int(project_id), "asset", 0
    if suffix == "generation/voice":
        return int(project_id), "structure", 0
    if suffix.startswith("generation/"):
        return int(project_id), "asset", int(values.get("asset_id") or values.get("character_asset_id") or 0)
    if suffix in {"agents/episode_script", "episodes/confirm_from_script"} and values.get("episode_number"):
        return int(project_id), "episode", int(values["episode_number"])
    if suffix in {"ai/chat", "skills/optimize"}:
        return None
    return int(project_id), "structure", 0


async def enforce_drama_write(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    """团队写操作必须持有当前资源租约，个人旧接口保持兼容。"""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    # 取消走任务操作权限，不依赖等待释放的生成内容租约。
    if request.url.path.endswith(("/cancel_generate", "/cancel_video_jobs")):
        return
    values = dict(request.query_params)
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            if isinstance(body, dict):
                values.update(body)
        except ValueError:
            return  # 请求模型将提供标准 JSON 校验错误。
    try:
        target = await write_target(db, request.url.path, values)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="无效的创作资源编号") from exc
    if not target:
        return
    project = await get_owned_drama_project(db, target[0], user)
    if project.team_id is None:
        # 与个人项目迁入串行，不能用迁入前的 identity-map 快照绕过团队租约。
        project = (await db.execute(select(DramaProject).where(DramaProject.id == project.id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="漫剧项目不存在")
        if project.team_id is not None:
            raise HTTPException(status_code=409, detail="项目空间已变化，请刷新后重试")
        return
    if request.method == "DELETE" and re.search(r"/projects/\d+$", request.url.path):
        await require_drama_project_manager(db, project, user.id)
    if re.search(r"/scripts/\d+$", request.url.path) and "episode_content" in values:
        raise HTTPException(status_code=409, detail="团队剧本请按集保存，避免覆盖其他成员的修改")
    session_id = request.headers.get("X-Edit-Session", "")
    token = request.headers.get("X-Edit-Token", "")
    try:
        version = int(request.headers.get("X-Edit-Version", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="无效的编辑版本") from exc
    if not session_id or not token:
        raise HTTPException(status_code=428, detail="请先获取此内容的编辑权限")
    await validate_lock(db, project, user.id, target[1], target[2], session_id, token, version)
    # 批量任务在本次路由内部创建；新的 HTTP 请求不能越过既有系统写回占用。
    await _task_conflicts(db, project.id, (target[1], target[2]))
    db.info.update(team_edit_session_id=session_id, team_edit_token=token, team_edit_version=version,
                   team_edit_resource=target[1:], team_edit_project_id=project.id)
