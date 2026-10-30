"""团队摘要、成员财务遮蔽与服务端分页查询。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.models_teams import Team, TeamActivity, TeamMember
from app.schemas_common import PageMeta
from app.schemas_teams import TeamActivityOut, TeamMemberOut, TeamPage, TeamSummary
from app.services.teams.access import get_team, member_role


async def summaries(db: AsyncSession, rows: list[tuple[Team, TeamMember]], user_id: int) -> list[TeamSummary]:
    """一次批量聚合成员、项目和资产数量，避免逐队 N+1 查询。"""
    if not rows:
        return []
    ids = [team.id for team, _ in rows]
    member_counts = dict((await db.execute(select(TeamMember.team_id, func.count()).where(TeamMember.team_id.in_(ids), TeamMember.active.is_(True)).group_by(TeamMember.team_id))).all())
    project_counts = dict((await db.execute(select(DramaProject.team_id, func.count()).where(DramaProject.team_id.in_(ids)).group_by(DramaProject.team_id))).all())
    asset_counts = dict((await db.execute(select(DramaProject.team_id, func.count(DramaAsset.id)).join(DramaAsset, DramaAsset.project_id == DramaProject.id).where(DramaProject.team_id.in_(ids)).group_by(DramaProject.team_id))).all())
    return [TeamSummary(
        id=team.id, name=team.name, description=team.description, color=team.color,
        created_at=team.created_at, updated_at=team.updated_at,
        my_role=member_role(team, member), viewer_id=user_id,
        member_count=member_counts.get(team.id, 0), project_count=project_counts.get(team.id, 0), asset_count=asset_counts.get(team.id, 0),
    ) for team, member in rows]


async def team_summary(db: AsyncSession, team_id: int, user_id: int) -> TeamSummary:
    """读取当前用户可访问的单队摘要。"""
    team, member = await get_team(db, team_id, user_id)
    return (await summaries(db, [(team, member)], user_id))[0]


async def list_teams(db: AsyncSession, user_id: int, page: int, page_size: int) -> TeamPage[TeamSummary]:
    """只列出当前用户仍有效加入的团队，排序与分页保持稳定。"""
    filters = (TeamMember.user_id == user_id, TeamMember.active.is_(True))
    total = int(await db.scalar(select(func.count()).select_from(TeamMember).where(*filters)) or 0)
    rows = (await db.execute(select(Team, TeamMember).join(TeamMember, TeamMember.team_id == Team.id).where(*filters).order_by(Team.updated_at.desc(), Team.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return TeamPage(items=await summaries(db, list(rows), user_id), meta=PageMeta(page=page, page_size=page_size, total=total))


async def member_outputs(db: AsyncSession, team: Team, viewer: TeamMember, rows: list[tuple[TeamMember, User]]) -> list[TeamMemberOut]:
    """按当前月预算账本聚合，并在序列化前遮蔽他人的财务字段。"""
    from app.models_team_billing import TeamMonthlyUsage
    from app.services.billing.team import billing_period

    user_ids = [member.user_id for member, _ in rows]
    period = billing_period()
    usages = (await db.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.team_id == team.id, TeamMonthlyUsage.user_id.in_(user_ids), TeamMonthlyUsage.period == period))).scalars().all() if user_ids else []
    by_user = {usage.user_id: usage for usage in usages}
    owner = team.owner_user_id == viewer.user_id
    result = []
    for member, user in rows:
        visible = owner or user.id == viewer.user_id
        usage = by_user.get(user.id)
        result.append(TeamMemberOut(
            id=user.id, nickname=user.nickname, email=user.email, avatar_url=user.avatar_url or "", role=member_role(team, member),
            monthly_limit_fen=member.monthly_limit_fen if visible else None,
            spent_fen=int(usage.spent_fen or 0) if visible and usage else 0 if visible else None,
            reserved_fen=int(usage.reserved_fen or 0) if visible and usage else 0 if visible else None,
            joined_at=member.joined_at,
        ))
    return result


async def list_members(db: AsyncSession, team_id: int, user_id: int, page: int, page_size: int, q: str = "", role: str | None = None) -> TeamPage[TeamMemberOut]:
    """成员筛选和分页在数据库执行，禁止返回已退出成员。"""
    team, viewer = await get_team(db, team_id, user_id)
    filters = [TeamMember.team_id == team_id, TeamMember.active.is_(True)]
    if q.strip():
        filters.append(or_(User.nickname.ilike(f"%{q.strip()}%"), User.email.ilike(f"%{q.strip()}%")))
    if role:
        filters.append(TeamMember.role == role)
    stmt = select(TeamMember, User).join(User, User.id == TeamMember.user_id).where(*filters)
    total = int(await db.scalar(select(func.count()).select_from(TeamMember).join(User, User.id == TeamMember.user_id).where(*filters)) or 0)
    rows = (await db.execute(stmt.order_by(TeamMember.joined_at.asc(), TeamMember.id.asc()).offset((page - 1) * page_size).limit(page_size))).all()
    return TeamPage(items=await member_outputs(db, team, viewer, list(rows)), meta=PageMeta(page=page, page_size=page_size, total=total))


async def list_activities(db: AsyncSession, team_id: int, user_id: int, page: int, page_size: int) -> TeamPage[TeamActivityOut]:
    """动态不因原项目或操作者退出而丢失。"""
    await get_team(db, team_id, user_id)
    total = int(await db.scalar(select(func.count()).select_from(TeamActivity).where(TeamActivity.team_id == team_id)) or 0)
    rows = (await db.execute(select(TeamActivity, User.nickname).join(User, User.id == TeamActivity.actor_user_id).where(TeamActivity.team_id == team_id).order_by(TeamActivity.created_at.desc(), TeamActivity.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return TeamPage(items=[TeamActivityOut(id=row.id, actor_user_id=row.actor_user_id, actor_nickname=nickname, action=row.action, subject=row.subject, project_id=row.project_id, created_at=row.created_at) for row, nickname in rows], meta=PageMeta(page=page, page_size=page_size, total=total))
