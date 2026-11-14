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


@router.post(
    "/acquire",
    summary="获取团队资源编辑租约",
    description=(
        "需要登录且为团队成员，为当前标签页获取指定项目资源的编辑租约，返回凭证、版本及过期时间。"
        "force=true 仅所有者或管理员可接管人工占用；"
        "冲突时由服务端拒绝。"
    ),
    response_description="编辑租约及持有凭证",
)
async def acquire(team_id: int, body: LockAcquire, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """需要登录且为团队成员，为当前标签页获取指定项目资源的编辑租约，返回凭证、版本及过期时间。force=true 仅所有者或管理员可接管人工占用；冲突时由服务端拒绝。"""
    return await acquire_lock(db, team_id, user.id, body.project_id, body.resource_type, body.resource_id, body.session_id, force=body.force)


@router.post(
    "/renew",
    summary="续租团队资源编辑权限",
    description="需要登录，使用当前用户及标签页持有的 token 和 session_id 续租。保留原凭证和版本，过期租约需重新获取。",
    response_description="续租后的编辑租约",
)
async def renew(team_id: int, body: LockToken, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """需要登录，使用当前用户及标签页持有的 token 和 session_id 续租。保留原凭证和版本，过期租约需重新获取。"""
    return await renew_lock(db, team_id, user.id, body.token, body.session_id)


@router.post(
    "/release",
    summary="释放团队资源编辑租约",
    description="需要登录，使用当前用户及标签页持有的 token 和 session_id 释放人工编辑占用。",
    response_description="释放后的租约状态",
)
async def release(team_id: int, body: LockToken, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """需要登录，使用当前用户及标签页持有的 token 和 session_id 释放人工编辑占用。"""
    return await renew_lock(db, team_id, user.id, body.token, body.session_id, release=True)


@router.get(
    "",
    summary="查询团队项目编辑占用",
    description="需要登录且为团队有效成员，返回指定项目当前未过期的人工编辑占用，包括资源、持有人和过期时间，不返回其他会话的租约凭证。",
    response_description="当前有效编辑占用列表",
)
async def list_locks(team_id: int, project_id: int = Query(gt=0, description="需要查看编辑占用的团队漫剧项目编号"), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """需要登录且为团队有效成员，返回指定项目当前未过期的人工编辑占用，包括资源、持有人和过期时间，不返回其他会话的租约凭证。"""
    from datetime import UTC, datetime

    await get_team(db, team_id, user.id)
    rows = (await db.execute(select(TeamEditLock).where(TeamEditLock.team_id == team_id, TeamEditLock.project_id == project_id, TeamEditLock.expires_at > datetime.now(UTC)))).scalars().all()
    return {"items": [await lock_view(db, row) for row in rows]}
