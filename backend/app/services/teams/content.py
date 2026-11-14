"""团队项目/资产查询、个人项目整体迁入和资产独立副本。"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.models_team_locks import TeamEditLock
from app.services.drama.asset_reference_views import clean_asset_reference_params
from app.services.drama.project_cover import resolve_drama_project_cover
from app.services.tasks.generation_references import public_references
from app.services.teams.access import get_team
from app.services.teams.activity import record_activity
from app.services.teams.content_media import copier_for

ACTIVE_STATUSES = ("pending", "leased", "running", "awaiting_poll", "awaiting_review", "cancel_requested", "finalizing")


def task_scope(project_id):
    """覆盖项目及历史仅挂子资源的任务，避免在途任务被漏判为空闲。"""
    episodes = select(DramaEpisode.id).where(DramaEpisode.project_id == project_id).correlate(DramaProject)
    return or_(TaskRun.drama_project_id == project_id,
        TaskRun.script_id.in_(select(DramaScript.id).where(DramaScript.project_id == project_id).correlate(DramaProject)),
        TaskRun.episode_id.in_(episodes),
        TaskRun.asset_id.in_(select(DramaAsset.id).where(DramaAsset.project_id == project_id).correlate(DramaProject)),
        TaskRun.fragment_id.in_(select(DramaEpisodeFragment.id).where(DramaEpisodeFragment.episode_id.in_(episodes)).correlate(DramaProject)))


def card_query():
    """项目状态由真实在途任务和分集视频就绪情况计算，筛选可直接在 SQL 执行。"""
    episode_count = select(func.count()).where(DramaEpisode.project_id == DramaProject.id).correlate(DramaProject).scalar_subquery()
    has_fragment = select(DramaEpisodeFragment.id).where(DramaEpisodeFragment.episode_id == DramaEpisode.id).correlate(DramaEpisode).exists()
    missing_video = select(DramaEpisodeFragment.id).where(DramaEpisodeFragment.episode_id == DramaEpisode.id, or_(DramaEpisodeFragment.video.is_(None), DramaEpisodeFragment.video == "")).correlate(DramaEpisode).exists()
    completed = select(func.count()).where(DramaEpisode.project_id == DramaProject.id, has_fragment, ~missing_video).correlate(DramaProject).scalar_subquery()
    active = select(TaskRun.id).where(task_scope(DramaProject.id), TaskRun.status.in_(ACTIVE_STATUSES)).correlate(DramaProject).exists()
    planned_text = DramaProject.params["episode_count"].as_string()
    planned = case((planned_text.op("~")(r"^[0-9]{1,3}$"), cast(planned_text, Integer)), else_=0)
    status = case((active, "producing"), (and_(episode_count > 0, completed == episode_count, episode_count >= planned), "completed"), else_="draft")
    return select(DramaProject, status.label("card_status"), episode_count.label("episode_count"), completed.label("completed_count")).options(selectinload(DramaProject.script), selectinload(DramaProject.assets), selectinload(DramaProject.episodes).selectinload(DramaEpisode.fragments)), status


async def cards(db, rows):
    """批量补充真实协作者和当前占用者，不从客户端 params 推断权限。"""
    ids = [project.id for project, *_ in rows]
    participants = (await db.execute(select(TaskRun.drama_project_id, TaskRun.requested_by).where(TaskRun.drama_project_id.in_(ids)).distinct())).all() if ids else []
    locks = (await db.execute(select(TeamEditLock).where(TeamEditLock.project_id.in_(ids), TeamEditLock.expires_at > datetime.now(UTC)).order_by(TeamEditLock.expires_at.desc()))).scalars().all() if ids else []
    result = []
    for project, status, count, completed in rows:
        params = project.params if isinstance(project.params, dict) else {}
        cover, _ = resolve_drama_project_cover(project)
        planned = params.get("episode_count")
        planned = planned if isinstance(planned, int) and planned >= 0 else 0
        result.append(dict(id=project.id, title=project.title, description=project.description or "", genre=str(params.get("genre") or "漫剧项目"), cover=cover or "", status=status, episodes=max(count, planned), completed_episodes=completed, updated_at=project.updated_at, collaborators=sorted({project.user_id, *(user_id for pid, user_id in participants if pid == project.id)}), editing_by=next((lock.user_id for lock in locks if lock.project_id == project.id), None)))
    return result


async def list_projects(db, team_id: int, user_id: int, page: int, page_size: int, q: str, status: str | None):
    """项目搜索和状态筛选均在分页之前执行。"""
    await get_team(db, team_id, user_id)
    stmt, status_expr = card_query()
    filters = [DramaProject.team_id == team_id]
    if q.strip():
        filters.append(or_(DramaProject.title.ilike(f"%{q.strip()}%"), DramaProject.description.ilike(f"%{q.strip()}%")))
    if status:
        filters.append(status_expr == status)
    total = int(await db.scalar(select(func.count()).select_from(DramaProject).where(*filters)) or 0)
    rows = (await db.execute(stmt.where(*filters).order_by(DramaProject.updated_at.desc(), DramaProject.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return dict(items=await cards(db, rows), meta=dict(page=page, page_size=page_size, total=total))


def asset_card(asset: DramaAsset) -> dict:
    """团队资产展示只返回必要字段，完整副本响应走现有 DramaAssetOut。"""
    params = asset.params if isinstance(asset.params, dict) else {}
    from app.services.drama.asset_metadata import current_media_metadata
    detail_params = {key: params[key] for key in ("visualPrompt", "voicePrompt", "description", "prompt") if key in params}
    detail_params["media_metadata"] = current_media_metadata(params)
    return dict(id=asset.id, name=asset.name or "未命名资产", type=asset.type, asset_type=asset.asset_type or "image", cover=asset.cover or "", url=asset.url or "", project_id=asset.project_id, description=str(params.get("description") or params.get("prompt") or ""), params=detail_params, updated_at=asset.updated_at)


async def list_assets(db, team_id: int, user_id: int, page: int, page_size: int, q: str, asset_type: str | None, project_id: int | None):
    """仅聚合当前团队项目资产，跨团队 project_id 不得用于探测资源。"""
    await get_team(db, team_id, user_id)
    if project_id is not None and not await db.scalar(select(DramaProject.id).where(DramaProject.id == project_id, DramaProject.team_id == team_id)):
        raise HTTPException(404, "团队项目不存在")
    filters = [DramaProject.team_id == team_id, DramaAsset.type.in_(("character", "scene", "prop", "voice"))]
    if q.strip():
        filters.append(DramaAsset.name.ilike(f"%{q.strip()}%"))
    if asset_type:
        filters.append(DramaAsset.type == asset_type)
    if project_id is not None:
        filters.append(DramaAsset.project_id == project_id)
    stmt = select(DramaAsset).join(DramaProject, DramaProject.id == DramaAsset.project_id).where(*filters)
    total = int(await db.scalar(select(func.count()).select_from(DramaAsset).join(DramaProject, DramaProject.id == DramaAsset.project_id).where(*filters)) or 0)
    rows = (await db.execute(stmt.order_by(DramaAsset.updated_at.desc(), DramaAsset.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    items = []
    for row in rows:
        item = asset_card(row)
        item["params"] = await clean_asset_reference_params(
            db, item["params"], user_id=user_id, team_id=team_id, project_id=row.project_id,
        )
        metadata = (item["params"] or {}).get("media_metadata", {})
        item["references"] = public_references(metadata.get("references")) if isinstance(metadata, dict) else []
        items.append(item)
    return dict(items=items, meta=dict(page=page, page_size=page_size, total=total))


async def busy_reasons(db, project_ids: list[int]) -> dict[int, str]:
    """一次批量判断个人项目的任务和存活租约。"""
    if not project_ids:
        return {}
    busy = (await db.execute(select(DramaProject.id).where(DramaProject.id.in_(project_ids), select(TaskRun.id).where(task_scope(DramaProject.id), TaskRun.status.in_(ACTIVE_STATUSES)).correlate(DramaProject).exists()))).scalars().all()
    leased = (await db.execute(select(TeamEditLock.project_id).where(TeamEditLock.project_id.in_(project_ids), TeamEditLock.expires_at > datetime.now(UTC)))).scalars().all()
    return {**{pid: "仍有编辑占用，请释放后再迁入" for pid in leased}, **{pid: "仍有生成任务，请等待结束后再迁入" for pid in busy}}


async def transferable_projects(db, team_id: int, user_id: int, page: int, page_size: int):
    """仅列出本人个人项目，附上可迁入状态和明确原因。"""
    await get_team(db, team_id, user_id)
    stmt, _ = card_query()
    filters = (DramaProject.user_id == user_id, DramaProject.team_id.is_(None))
    total = int(await db.scalar(select(func.count()).select_from(DramaProject).where(*filters)) or 0)
    rows = (await db.execute(stmt.where(*filters).order_by(DramaProject.updated_at.desc(), DramaProject.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    items = await cards(db, rows)
    reasons = await busy_reasons(db, [item["id"] for item in items])
    return dict(items=[{**item, "transferable": item["id"] not in reasons, "reason": reasons.get(item["id"], "")} for item in items], meta=dict(page=page, page_size=page_size, total=total))


def normalize_episode_numbers(script: DramaScript | None, episodes: list[DramaEpisode]) -> None:
    """保留有效集号与所有正文，仅为缺号/重号分配空闲编号。"""
    content = script.episode_content if script else None
    rows = content.get("episodes", []) if isinstance(content, dict) else content
    rows = [dict(row) for row in (rows or []) if isinstance(row, dict)]
    reserved = {row["episodeNumber"] for row in rows if isinstance(row.get("episodeNumber"), int) and row["episodeNumber"] > 0}
    used = set()
    for row in rows:
        number = row.get("episodeNumber")
        if not isinstance(number, int) or number < 1 or number in used:
            number = 1
            while number in used or number in reserved:
                number += 1
        row["episodeNumber"] = number
        used.add(number)
    if script and content is not None:
        script.episode_content = {**content, "episodes": rows} if isinstance(content, dict) else rows
    used = set()
    for index, episode in enumerate(sorted(episodes, key=lambda item: item.id)):
        params = dict(episode.params or {})
        number = params.get("episodeNumber")
        if not isinstance(number, int) or number < 1 or number in used:
            number = rows[index]["episodeNumber"] if index < len(rows) and rows[index]["episodeNumber"] not in used else 1
            while number in used:
                number += 1
        used.add(number)
        episode.params = {**params, "episodeNumber": number}


async def transfer_project(db, team_id: int, user_id: int, project_id: int):
    """整体原地迁入本人空闲项目，历史任务/用量的个人计费归属不变。"""
    await get_team(db, team_id, user_id, lock=True)
    project = (await db.execute(select(DramaProject).where(DramaProject.id == project_id, DramaProject.user_id == user_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not project:
        raise HTTPException(404, "个人项目不存在")
    if project.team_id == team_id:
        return project, None
    if project.team_id is not None:
        raise HTTPException(409, "项目已属于其他团队，不能再次迁入")
    reasons = await busy_reasons(db, [project_id])
    if project_id in reasons:
        raise HTTPException(409, reasons[project_id])
    script = (await db.execute(select(DramaScript).where(DramaScript.project_id == project_id).with_for_update())).scalar_one_or_none()
    episodes = list((await db.execute(select(DramaEpisode).where(DramaEpisode.project_id == project_id).with_for_update())).scalars().all())
    assets = list((await db.execute(select(DramaAsset).where(DramaAsset.project_id == project_id).with_for_update())).scalars().all())
    fragments = list((await db.execute(select(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id.in_([episode.id for episode in episodes])).with_for_update())).scalars().all()) if episodes else []
    copier = await copier_for(db, project, transfer=True)
    try:
        project.content, project.params = await copier.rewrite(project.content), await copier.rewrite(project.params)
        if script:
            script.summary, script.episode_content, script.params = await copier.rewrite(script.summary), await copier.rewrite(script.episode_content), await copier.rewrite(script.params)
        for asset in assets:
            asset.cover, asset.url, asset.params = await copier.rewrite(asset.cover), await copier.rewrite(asset.url), await copier.rewrite(asset.params)
        for episode in episodes:
            episode.params = await copier.rewrite(episode.params)
        for fragment in fragments:
            fragment.cover, fragment.video, fragment.params = await copier.rewrite(fragment.cover), await copier.rewrite(fragment.video), await copier.rewrite(fragment.params)
        normalize_episode_numbers(script, episodes)
        project.team_id = team_id
        await record_activity(db, team_id, user_id, "project.transferred", project.title, project_id=project_id)
        return project, copier
    except Exception:
        copier.cleanup()
        raise


async def copy_asset(db, team_id: int | None, user_id: int, asset_id: int, target_id: int, session_id: str = "", token: str = "", version: int = 0):
    """源目标都按当前团队授权；持目标新资产租约后独立复制内容与本地文件。"""
    from app.services.teams.edit_locks import _task_conflicts, validate_lock

    if team_id is not None:
        await get_team(db, team_id, user_id, lock=True)
    scope = DramaProject.team_id == team_id if team_id is not None else and_(DramaProject.team_id.is_(None), DramaProject.user_id == user_id)
    asset = (await db.execute(select(DramaAsset).join(DramaProject, DramaProject.id == DramaAsset.project_id).where(DramaAsset.id == asset_id, scope))).scalar_one_or_none()
    target = (await db.execute(select(DramaProject).where(DramaProject.id == target_id, scope).with_for_update())).scalar_one_or_none()
    if not asset or not target:
        raise HTTPException(404, "团队资产或目标项目不存在")
    if team_id is not None:
        if not session_id or not token:
            raise HTTPException(428, "请先获取目标项目的资产编辑权限")
        await validate_lock(db, target, user_id, "asset", 0, session_id, token, version)
    await _task_conflicts(db, target_id, ("asset", 0))
    copier = await copier_for(db, target)
    try:
        params = await copier.rewrite(asset.params or {}, clean=True)
        from app.services.drama.asset_metadata import current_media_metadata
        params["media_metadata"] = await copier.rewrite(current_media_metadata(asset.params))
        params["copy_origin"] = {"asset_id": asset.id, "project_id": asset.project_id}
        clone = DramaAsset(project_id=target_id, type=asset.type, asset_type=asset.asset_type, name=asset.name, cover=await copier.rewrite(asset.cover), url=await copier.rewrite(asset.url), params=params, derive_id=None)
        db.add(clone)
        await db.flush()
        if team_id is not None:
            await record_activity(db, team_id, user_id, "asset.copied", asset.name or "资产", project_id=target_id)
        return clone, copier
    except Exception:
        copier.cleanup()
        raise
