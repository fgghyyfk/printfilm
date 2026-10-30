"""Compatibility handoff from historical creation sessions to the project editor."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ProjectOut
from app.services import creation

router = APIRouter(prefix="/creation-sessions", tags=["projects"])


@router.post(
    "/{session_id}/open-editor",
    response_model=ProjectOut,
    summary="将历史创作项目转入分步编辑器",
    description=(
        "历史项目兼容接口，需要登录，将本人历史创作会话的指定制作项目或最新可用项目转入分步编辑器；"
        "尚无制作项目时根据历史会话内容创建项目。存在未结束或尚未结算的生产任务时返回 409，需等待后重试；"
        "成功后项目交由分步编辑器继续编辑。"
    ),
    response_description="交接后的项目详情",
)
async def open_editor(
    session_id: int,
    project_id: int | None = Query(
        None, ge=1,
        description="要转入分步编辑器的历史会话制作项目编号；不传时使用最新可用项目或根据历史会话内容创建",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await creation.open_editor(db, user, session_id, project_id=project_id)
