"""Drama script endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_drama import DramaScriptOut
from app.services.drama.access import get_owned_drama_project
from app.services.teams.script_updates import locked_script, patch_episode_body

router = APIRouter()


class ScriptUpdateBody(BaseModel):
    source: str | None = None
    summary: dict | None = None
    episode_content: dict | list | None = None
    image_style_id: str | None = None
    name: str | None = None


@router.get("/scripts/{project_id}", response_model=DramaScriptOut)
async def get_script(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaScriptOut:
    project = await get_owned_drama_project(db, project_id, user, with_script=True)
    if not project.script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    return DramaScriptOut.model_validate(project.script)


@router.patch("/scripts/{project_id}", response_model=DramaScriptOut)
async def update_script(
    project_id: int,
    body: ScriptUpdateBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaScriptOut:
    project = await get_owned_drama_project(db, project_id, user, with_script=True)
    if not project.script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    script = await locked_script(db, project_id) if project.team_id is not None else project.script
    if body.source is not None:
        script.source = body.source
    if body.summary is not None:
        prev_summary = script.summary if isinstance(script.summary, dict) else {}
        script.summary = body.summary
        from app.services.drama.agents import normalize_series_title, pick_auto_project_title

        new_series = normalize_series_title(body.summary.get("seriesTitle"))
        old_series = normalize_series_title(prev_summary.get("seriesTitle"))
        # 用户在摘要里主动改了剧名 → 同步项目名；否则仅覆盖「默认/截断」标题
        if new_series and new_series != old_series:
            project.title = new_series
            script.name = new_series
        else:
            auto_title = pick_auto_project_title(
                body.summary,
                creative=(script.source or "").strip(),
                current_title=project.title or "",
            )
            if auto_title:
                project.title = auto_title
                script.name = auto_title
    if body.episode_content is not None:
        script.episode_content = body.episode_content
    if body.name is not None:
        script.name = body.name
    if body.image_style_id is not None:
        params = dict(script.params or {})
        params["image_style_id"] = body.image_style_id
        script.params = params
        pparams = dict(project.params or {})
        pparams["image_style_id"] = body.image_style_id
        project.params = pparams
    await db.commit()
    await db.refresh(script)
    return DramaScriptOut.model_validate(script)


class EpisodeScriptPatch(BaseModel):
    """省略字段保持不变，空字符串明确清空；版本可用于同会话的陈旧保存检测。"""
    title: str | None = Field(default=None, max_length=200)
    creative: str | None = Field(default=None, max_length=20000)
    summary: str | None = Field(default=None, max_length=50000)
    body: str | None = Field(default=None, max_length=100000)
    version: int | None = Field(default=None, ge=0)


@router.patch("/scripts/{project_id}/episodes/{episode_number}")
async def update_episode_script(project_id: int, episode_number: int, body: EpisodeScriptPatch, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """按集原子保存，所有成员在不同集的修改互不覆盖。"""
    project = await get_owned_drama_project(db, project_id, user)
    if project.team_id is not None and body.version is None:
        raise HTTPException(status_code=428, detail="请携带读取时的分集内容版本后保存")
    if not 1 <= episode_number <= 120:
        raise HTTPException(status_code=422, detail="无效的分集编号")
    item = await patch_episode_body(db, project_id, episode_number, body.model_dump(exclude_unset=True))
    await db.commit()
    return item
