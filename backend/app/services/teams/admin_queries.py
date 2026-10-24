"""平台团队只读查询；集合聚合避免一对多联接重复金额或逐行查询。"""
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent, User
from app.models_drama import DramaProject
from app.models_teams import Team, TeamMember
from app.models_team_billing import TeamMonthlyUsage, TeamWallet
from app.schemas_admin_teams import (
    AdminTeamMemberOut, AdminTeamMemberPage, AdminTeamOut, AdminTeamPage,
    AdminTeamUsageOut, AdminTeamUsagePage, AdminTeamUsageSummary,
)
from app.schemas_common import PageMeta
from app.services import billing
from app.services.billing.team import SHANGHAI, billing_period


async def require_team(db: AsyncSession, team_id: int) -> None:
    """统一不存在团队的详情/子列表行为，不创建钱包或成员关系。"""
    if await db.scalar(select(Team.id).where(Team.id == team_id)) is None:
        raise HTTPException(404, "团队不存在")


def _team_statement(period: str):
    """先分别按团队聚合，再与团队主体联接，保证钱包与消费不会倍增。"""
    # members/projects/usage 分别为有效成员数、项目数、实际已结算月度金额。
    members = select(TeamMember.team_id, func.count().label("member_count")).where(TeamMember.active.is_(True)).group_by(TeamMember.team_id).subquery()
    projects = select(DramaProject.team_id, func.count().label("project_count")).where(DramaProject.team_id.is_not(None)).group_by(DramaProject.team_id).subquery()
    usage = select(
        TeamMonthlyUsage.team_id,
        func.sum(TeamMonthlyUsage.spent_fen).label("total_spent_fen"),
        func.sum(case((TeamMonthlyUsage.period == period, TeamMonthlyUsage.spent_fen), else_=0)).label("month_spent_fen"),
        func.sum(case((TeamMonthlyUsage.period == period, TeamMonthlyUsage.reserved_fen), else_=0)).label("month_reserved_fen"),
    ).group_by(TeamMonthlyUsage.team_id).subquery()
    return select(
        Team.id, Team.name, Team.description, Team.color, Team.owner_user_id, Team.created_at, Team.monthly_limit_fen,
        User.nickname.label("owner_nickname"), User.email.label("owner_email"),
        func.coalesce(members.c.member_count, 0).label("member_count"),
        func.coalesce(projects.c.project_count, 0).label("project_count"),
        func.coalesce(User.balance_fen, 0).label("balance_fen"),
        func.coalesce(User.frozen_fen, 0).label("frozen_fen"),
        func.coalesce(usage.c.month_spent_fen, 0).label("month_spent_fen"),
        func.coalesce(usage.c.month_reserved_fen, 0).label("month_reserved_fen"),
        func.coalesce(usage.c.total_spent_fen, 0).label("total_spent_fen"),
    ).join(User, User.id == Team.owner_user_id).outerjoin(TeamWallet, TeamWallet.team_id == Team.id).outerjoin(
        members, members.c.team_id == Team.id,
    ).outerjoin(projects, projects.c.team_id == Team.id).outerjoin(usage, usage.c.team_id == Team.id)


async def list_teams(db: AsyncSession, *, q: str, owner_id: int | None, page: int, page_size: int) -> AdminTeamPage:
    """搜索团队名称、ID或所有者，列表与总数共用筛选条件。"""
    # period 固定本次请求的上海自然月；filters 避免总数与列表口径漂移。
    period, filters = billing_period(), []
    if q.strip():
        raw = q.strip()
        match = or_(Team.name.ilike(f"%{raw}%"), User.email.ilike(f"%{raw}%"), User.nickname.ilike(f"%{raw}%"))
        if raw.isdecimal() and len(raw) <= 10 and int(raw) <= 2_147_483_647:
            match = or_(match, Team.id == int(raw))
        filters.append(match)
    if owner_id is not None:
        filters.append(Team.owner_user_id == owner_id)
    total = int(await db.scalar(select(func.count()).select_from(Team).join(User, User.id == Team.owner_user_id).where(*filters)) or 0)
    rows = (await db.execute(_team_statement(period).where(*filters).order_by(Team.id.desc()).offset((page - 1) * page_size).limit(page_size))).mappings().all()
    return AdminTeamPage(items=[AdminTeamOut(**row, period=period) for row in rows], meta=PageMeta(page=page, page_size=page_size, total=total))


async def team_detail(db: AsyncSession, team_id: int) -> AdminTeamOut:
    """读取任意团队快照，平台管理员无需加入团队。"""
    period = billing_period()
    row = (await db.execute(_team_statement(period).where(Team.id == team_id))).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "团队不存在")
    return AdminTeamOut(**row, period=period)


async def list_members(db: AsyncSession, team_id: int, *, q: str, role: str | None, state: str, period: str | None, page: int, page_size: int) -> AdminTeamMemberPage:
    """分页查看当前及历史成员；消费记录按选定月份而非当前资格保留。"""
    await require_team(db, team_id)
    month = period or billing_period()
    filters = [TeamMember.team_id == team_id]
    if state != "all":
        filters.append(TeamMember.active.is_(state == "active"))
    if role:
        filters.append(TeamMember.role == role)
    if q.strip():
        filters.append(or_(User.nickname.ilike(f"%{q.strip()}%"), User.email.ilike(f"%{q.strip()}%")))
    total = int(await db.scalar(select(func.count()).select_from(TeamMember).join(User, User.id == TeamMember.user_id).where(*filters)) or 0)
    rows = (await db.execute(select(
        TeamMember, User.nickname, User.email,
        func.coalesce(TeamMonthlyUsage.spent_fen, 0), func.coalesce(TeamMonthlyUsage.reserved_fen, 0),
    ).join(User, User.id == TeamMember.user_id).outerjoin(TeamMonthlyUsage, (
        (TeamMonthlyUsage.team_id == team_id) & (TeamMonthlyUsage.user_id == User.id) & (TeamMonthlyUsage.period == month)
    )).where(*filters).order_by(TeamMember.id).offset((page - 1) * page_size).limit(page_size))).all()
    return AdminTeamMemberPage(items=[AdminTeamMemberOut(
        user_id=m.user_id, nickname=nickname, email=email, role=m.role, active=m.active,
        monthly_limit_fen=m.monthly_limit_fen, spent_fen=spent, reserved_fen=reserved, joined_at=m.joined_at,
    ) for m, nickname, email, spent, reserved in rows], meta=PageMeta(page=page, page_size=page_size, total=total), period=month)


async def list_usage(db: AsyncSession, team_id: int, *, q: str, user_id: int | None, settled: bool | None, date_from: date | None, date_to: date | None, page: int, page_size: int) -> AdminTeamUsagePage:
    """按团队账务归属查消费，退出成员和删除项目的历史记录仍可查看。"""
    await require_team(db, team_id)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    filters = [UsageEvent.team_id == team_id]
    if user_id is not None:
        filters.append(UsageEvent.user_id == user_id)
    if settled is not None:
        filters.append(UsageEvent.settled.is_(settled))
    if date_from:
        filters.append(UsageEvent.created_at >= datetime.combine(date_from, time.min, SHANGHAI))
    if date_to == date.max:
        filters.append(UsageEvent.created_at <= datetime.combine(date_to, time.max, SHANGHAI))
    elif date_to:
        # 使用次日排他边界，包含结束日最后一秒的全部小数精度。
        filters.append(UsageEvent.created_at < datetime.combine(date_to, time.min, SHANGHAI) + timedelta(days=1))
    if q.strip():
        raw = f"%{q.strip()}%"
        filters.append(or_(User.email.ilike(raw), User.nickname.ilike(raw), DramaProject.title.ilike(raw), UsageEvent.model.ilike(raw), UsageEvent.billing_key.ilike(raw)))
    base = select(UsageEvent).outerjoin(User, User.id == UsageEvent.user_id).outerjoin(DramaProject, DramaProject.id == UsageEvent.drama_project_id).where(*filters)
    filtered = base.subquery()
    summary = (await db.execute(select(func.count(), func.coalesce(func.sum(case((filtered.c.settled.is_(True), filtered.c.charge_fen), else_=0)), 0)).select_from(filtered))).one()
    rows = (await db.execute(base.with_only_columns(
        UsageEvent, User.nickname, User.email, DramaProject.title,
    ).order_by(UsageEvent.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return AdminTeamUsagePage(items=[AdminTeamUsageOut(
        id=ev.id, user_id=ev.user_id, user_nickname=nickname or "", user_email=email or "",
        project_id=ev.drama_project_id, project_title=title, task_run_id=ev.task_run_id,
        billing_key=ev.billing_key, billing_label=billing.billing_key_label(ev.billing_key),
        model=ev.model or "", total_tokens=int(ev.total_tokens or 0), charge_fen=int(ev.charge_fen or 0),
        settled=ev.settled, created_at=ev.created_at,
    ) for ev, nickname, email, title in rows], meta=PageMeta(page=page, page_size=page_size, total=summary[0]),
        summary=AdminTeamUsageSummary(record_count=summary[0], settled_charge_fen=summary[1]))
