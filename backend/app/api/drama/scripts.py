"""Drama script endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
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
    """剧本更新字段，省略或传 null 保持原值。"""

    source: str | None = Field(default=None, description="原始创意文案，空字符串可清空")
    summary: dict | None = Field(default=None, description="整体替换剧本摘要；修改 seriesTitle 时会同步项目标题和剧本名称")
    episode_content: dict | list | None = Field(default=None, description="整体替换分集剧本；团队项目禁止通过此字段保存，须使用按集保存接口")
    image_style_id: str | None = Field(default=None, description="图片风格标识，同时更新剧本和项目配置")
    name: str | None = Field(default=None, description="剧本名称，在摘要引起的自动命名之后应用")


@router.get(
    "/scripts/{project_id}", response_model=DramaScriptOut,
    summary="获取项目剧本",
    description="按项目编号 project_id 读取可访问项目的剧本，包含创意、摘要、分集正文及生成状态；项目没有剧本时返回 404。",
    response_description="项目关联剧本及其当前生成状态",
)
async def get_script(
    project_id: Annotated[int, Path(description="项目编号，需有该项目的个人或团队访问权限")],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaScriptOut:
    project = await get_owned_drama_project(db, project_id, user, with_script=True)
    if not project.script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    return DramaScriptOut.model_validate(project.script)


@router.patch(
    "/scripts/{project_id}", response_model=DramaScriptOut,
    summary="更新项目剧本",
    description="按项目编号 project_id 更新剧本，省略或传 null 的字段保持原值，摘要与分集内容整体替换。摘要中的剧名变化可能同步项目标题；图片风格同步保存到项目与剧本配置。团队项目需持有剧本编辑租约，且禁止全量写入 episode_content，应改用按集保存接口。",
    response_description="保存后的剧本内容和配置",
)
async def update_script(
    project_id: Annotated[int, Path(description="项目编号，需有该项目的个人或团队访问权限")],
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
    title: str | None = Field(default=None, max_length=200, description="本集标题，空字符串明确清空")
    creative: str | None = Field(default=None, max_length=20000, description="本集创意或创作备注，空字符串明确清空")
    summary: str | None = Field(default=None, max_length=50000, description="本集摘要，空字符串明确清空")
    body: str | None = Field(default=None, max_length=100000, description="本集剧本正文，空字符串明确清空")
    version: int | None = Field(default=None, ge=0, description="读取时的分集内容版本，团队保存必须提供，用于检测陈旧写入")


@router.patch(
    "/scripts/{project_id}/episodes/{episode_number}",
    summary="按集保存剧本内容",
    description="按项目编号 project_id 和分集序号 episode_number 原子保存一集，序号范围为 1 至 120；省略字段保持原值，空字符串明确清空。团队项目需持有本集编辑租约，并提供读取时的 version，缺少版本返回 428，陈旧版本无法覆盖最新内容。",
    response_description="保存后的单集剧本记录，包含更新后的内容版本",
)
async def update_episode_script(project_id: Annotated[int, Path(description="项目编号，需有该项目的个人或团队访问权限")], episode_number: Annotated[int, Path(description="分集序号，从 1 开始，范围为 1 至 120")], body: EpisodeScriptPatch, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """按集原子保存，所有成员在不同集的修改互不覆盖。"""
    project = await get_owned_drama_project(db, project_id, user)
    if project.team_id is not None and body.version is None:
        raise HTTPException(status_code=428, detail="请携带读取时的分集内容版本后保存")
    if not 1 <= episode_number <= 120:
        raise HTTPException(status_code=422, detail="无效的分集编号")
    item = await patch_episode_body(db, project_id, episode_number, body.model_dump(exclude_unset=True))
    await db.commit()
    return item
