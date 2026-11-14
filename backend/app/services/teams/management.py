"""团队与成员变更，所有写操作沿团队锁顺序同事务执行。"""
from __future__ import annotations

from datetime import UTC, datetime
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_teams import Team, TeamMember, TeamInvitation
from app.schemas_teams import TeamCreate, TeamMemberPatch, TeamUpdate
from app.services.teams.access import get_team, member_role
from app.services.teams.activity import record_activity


async def create_team(db: AsyncSession, user_id: int, body: TeamCreate) -> Team:
    """创建团队及唯一所有者，费用直接使用所有者个人余额。"""
    team = Team(owner_user_id=user_id, **body.model_dump())
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user_id, role="owner", active=True))
    await record_activity(db, team.id, user_id, "team.created", team.name)
    return team


async def update_team(db: AsyncSession, team_id: int, user_id: int, body: TeamUpdate) -> None:
    """仅所有者修改团队资料。"""
    team, _ = await get_team(db, team_id, user_id, roles=("owner",), lock=True)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(team, key, value)
    await record_activity(db, team_id, user_id, "team.updated", team.name)


async def delete_team(db: AsyncSession, team_id: int, user_id: int) -> None:
    """所有者解散团队；保留历史归属，撤销所有成员和邀请访问。"""
    from app.models_tasks import TaskRun
    from app.services.tasks.service import TERMINAL_TASK_STATUSES
    from app.services.teams.edit_locks import release_member_locks

    team, _ = await get_team(db, team_id, user_id, roles=("owner",), lock=True)
    active_task = await db.scalar(select(TaskRun.id).where(
        TaskRun.team_id == team_id, TaskRun.status.not_in(TERMINAL_TASK_STATUSES),
    ).limit(1))
    if active_task is not None:
        raise HTTPException(409, "团队还有未结束的生成任务，请等待完成或取消任务后再删除")
    members = (await db.execute(select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.active.is_(True),
    ).order_by(TeamMember.id).with_for_update())).scalars().all()
    for member in members:
        member.active = False
        await release_member_locks(db, team_id, member.user_id)
    await db.execute(update(TeamInvitation).where(
        TeamInvitation.team_id == team_id,
        TeamInvitation.accepted_at.is_(None), TeamInvitation.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(UTC)))
    await record_activity(db, team_id, user_id, "team.deleted", team.name)


async def active_member(db: AsyncSession, team_id: int, user_id: int) -> TeamMember:
    """在团队已锁定的事务中锁定目标有效成员。"""
    member = (await db.execute(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id, TeamMember.active.is_(True)).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not member:
        raise HTTPException(404, "团队成员不存在")
    return member


async def update_member(db: AsyncSession, team_id: int, actor_id: int, user_id: int, body: TeamMemberPatch) -> None:
    """所有者调整成员月额度或非所有者角色，不重置历史消费。"""
    team, _ = await get_team(db, team_id, actor_id, roles=("owner",), lock=True)
    member = await active_member(db, team_id, user_id)
    if team.owner_user_id == user_id and "role" in body.model_fields_set:
        raise HTTPException(409, "不能修改所有者的角色，请先转让所有权")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(member, key, value)
    user = await db.get(User, user_id)
    await record_activity(db, team_id, actor_id, "member.updated", user.nickname if user else str(user_id))


async def deactivate_member(db: AsyncSession, team_id: int, actor_id: int, member: TeamMember, action: str) -> None:
    """成员软退出与任务/编辑锁清理处于同一事务，不提前退还在途费用。"""
    from app.services.tasks.team_scope import cancel_removed_member_tasks
    from app.services.teams.edit_locks import release_member_locks

    member.active = False
    await db.flush()
    await cancel_removed_member_tasks(db, team_id, member.user_id)
    await release_member_locks(db, team_id, member.user_id)
    user = await db.get(User, member.user_id)
    await record_activity(db, team_id, actor_id, action, user.nickname if user else str(member.user_id))


async def remove_member(db: AsyncSession, team_id: int, actor_id: int, user_id: int) -> None:
    """管理员仅能移除普通成员；不能用移除入口移除自己或所有者。"""
    team, actor = await get_team(db, team_id, actor_id, roles=("owner", "admin"), lock=True)
    member = await active_member(db, team_id, user_id)
    if actor_id == user_id:
        raise HTTPException(409, "请使用退出团队功能")
    if member_role(team, actor) == "admin" and member_role(team, member) != "member":
        raise HTTPException(403, "管理员只能移除普通成员")
    if team.owner_user_id == user_id:
        raise HTTPException(409, "不能移除团队所有者")
    await deactivate_member(db, team_id, actor_id, member, "member.removed")


async def leave_team(db: AsyncSession, team_id: int, user_id: int) -> None:
    """所有者必须先转让；普通成员退出保留内容、账单和成员关系。"""
    team, member = await get_team(db, team_id, user_id, lock=True)
    if team.owner_user_id == user_id:
        raise HTTPException(409, "请先转让所有权，再退出团队")
    await deactivate_member(db, team_id, user_id, member, "member.left")


async def transfer_ownership(db: AsyncSession, team_id: int, actor_id: int, user_id: int) -> None:
    """持有团队与两个成员行锁转让，先降级旧所有者再提升新所有者。"""
    team, actor = await get_team(db, team_id, actor_id, roles=("owner",), lock=True)
    if actor_id == user_id:
        raise HTTPException(409, "请选择其他团队成员接任所有者")
    target = await active_member(db, team_id, user_id)
    actor.role = "admin"
    await db.flush()
    team.owner_user_id = target.user_id
    target.role = "owner"
    await db.flush()
    user = await db.get(User, user_id)
    await record_activity(db, team_id, actor_id, "team.ownership_transferred", user.nickname if user else str(user_id))
