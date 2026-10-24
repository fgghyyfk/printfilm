"""团队编辑占用 HTTP 接口。"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.models_team_locks import TeamEditLock
from app.services.teams.access import get_team
from app.services.teams.edit_locks import acquire_lock, lock_view, renew_lock

router = APIRouter(prefix="/teams/{team_id}/edit-locks", tags=["teams"])


class LockAcquire(BaseModel):
    """会话标识属于当前标签页，真实用户来自认证凭证。"""
    project_id: int = Field(gt=0)
    resource_type: Literal["structure", "script", "episode", "asset", "canvas"]
    resource_id: int = Field(default=0, ge=0)
    session_id: str = Field(min_length=1, max_length=80)
    force: bool = False


class LockToken(BaseModel):
    """续租与释放都要证明持有原始凭证。"""
    token: str = Field(min_length=20, max_length=100)
    session_id: str = Field(min_length=1, max_length=80)


@router.post("/acquire")
async def acquire(team_id: int, body: LockAcquire, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """获取资源租约或由负责人接管人工占用。"""
    return await acquire_lock(db, team_id, user.id, body.project_id, body.resource_type, body.resource_id, body.session_id, force=body.force)


@router.post("/renew")
async def renew(team_id: int, body: LockToken, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """续租保持原 token/版本，过期后需重新获取。"""
    return await renew_lock(db, team_id, user.id, body.token, body.session_id)


@router.post("/release")
async def release(team_id: int, body: LockToken, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """释放当前标签页持有的人工资源占用。"""
    return await renew_lock(db, team_id, user.id, body.token, body.session_id, release=True)


@router.get("")
async def list_locks(team_id: int, project_id: int = Query(gt=0), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """查看团队项目当前人工占用，不暴露其他会话凭证。"""
    from datetime import UTC, datetime

    await get_team(db, team_id, user.id)
    rows = (await db.execute(select(TeamEditLock).where(TeamEditLock.team_id == team_id, TeamEditLock.project_id == project_id, TeamEditLock.expires_at > datetime.now(UTC)))).scalars().all()
    return {"items": [await lock_view(db, row) for row in rows]}
