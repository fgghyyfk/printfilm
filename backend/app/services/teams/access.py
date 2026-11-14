"""团队授权统一入口，成员变更后每次请求实时读取数据库。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_teams import Team, TeamMember


def member_role(team: Team, member: TeamMember) -> str:
    """权威所有者字段优先，错误的 owner 成员标记不能产生越权。"""
    if team.owner_user_id == member.user_id:
        return "owner"
    return "admin" if member.role == "admin" else "member"


async def get_team(
    db: AsyncSession, team_id: int, user_id: int, *,
    roles: tuple[str, ...] | None = None, lock: bool = False,
) -> tuple[Team, TeamMember]:
    """先锁团队再锁成员；不在团队内返回 404，角色不足返回 403。"""
    team_stmt = select(Team).where(Team.id == team_id).execution_options(populate_existing=True)
    if lock:
        team_stmt = team_stmt.with_for_update()
    team = (await db.execute(team_stmt)).scalar_one_or_none()
    if not team:
        raise HTTPException(404, "团队不存在")
    member_stmt = select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id, TeamMember.active.is_(True),
    ).execution_options(populate_existing=True)
    if lock:
        member_stmt = member_stmt.with_for_update()
    member = (await db.execute(member_stmt)).scalar_one_or_none()
    if not member:
        raise HTTPException(404, "团队不存在或已退出")
    if roles is not None and member_role(team, member) not in roles:
        raise HTTPException(403, "没有团队操作权限")
    return team, member
