"""团队核心路由：身份来自现有认证，权限与变更交由团队服务。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_teams import (
    TeamActivityOut, TeamCreate, TeamInvitationCreated, TeamInvitationInspect,
    TeamInvitationOut, TeamInvitationToken, TeamInviteCreate, TeamMemberOut,
    TeamMemberPatch, TeamPage, TeamSummary, TeamTransfer, TeamUpdate,
)
from app.services.teams import invitations, management, queries
from app.services.teams.access import get_team

router = APIRouter(prefix="/teams", tags=["teams"])
invitation_router = APIRouter(prefix="/team-invitations", tags=["team-invitations"])


@router.get("", response_model=TeamPage[TeamSummary])
async def list_teams(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamSummary]:
    """分页列出当前用户有效加入的团队。"""
    return await queries.list_teams(db, user.id, page, page_size)


@router.post("", response_model=TeamSummary, status_code=201)
async def create_team(
    body: TeamCreate, response: Response,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """创建由所有者个人账户支付费用的团队。"""
    team = await management.create_team(db, user.id, body)
    await db.commit()
    response.headers["Location"] = f"/api/teams/{team.id}"
    return await queries.team_summary(db, team.id, user.id)


@router.get("/{team_id}", response_model=TeamSummary)
async def get_team_summary(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """团队摘要不暴露其他空间的数据。"""
    return await queries.team_summary(db, team_id, user.id)


@router.patch("/{team_id}", response_model=TeamSummary)
async def update_team(
    team_id: int, body: TeamUpdate,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """所有者修改团队资料或月度消费上限。"""
    await management.update_team(db, team_id, user.id, body)
    await db.commit()
    return await queries.team_summary(db, team_id, user.id)


@router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> Response:
    """仅所有者解散团队，历史作品与账单保留归档。"""
    await management.delete_team(db, team_id, user.id)
    await db.commit()
    return Response(status_code=204)


@router.get("/{team_id}/members", response_model=TeamPage[TeamMemberOut])
async def list_members(
    team_id: int, q: str = Query("", max_length=254), role: Literal["owner", "admin", "member"] | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamMemberOut]:
    """成员列表由服务端筛选、分页并遮蔽不可见财务字段。"""
    return await queries.list_members(db, team_id, user.id, page, page_size, q, role)


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberOut)
async def update_member(
    team_id: int, user_id: int, body: TeamMemberPatch,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamMemberOut:
    """所有者调整成员月额度或非所有者角色。"""
    await management.update_member(db, team_id, user.id, user_id, body)
    await db.commit()
    team, viewer = await get_team(db, team_id, user.id)
    member = await management.active_member(db, team_id, user_id)
    target = await db.get(User, user_id)
    return (await queries.member_outputs(db, team, viewer, [(member, target)]))[0]


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: int, user_id: int,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """软移除成员，同事务清理未开始任务与人工编辑锁。"""
    await management.remove_member(db, team_id, user.id, user_id)
    await db.commit()
    return {"ok": True}


@router.post("/{team_id}/leave")
async def leave_team(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """当前成员退出团队，内容和历史消费继续保留。"""
    await management.leave_team(db, team_id, user.id)
    await db.commit()
    return {"ok": True}


@router.post("/{team_id}/transfer-ownership", response_model=TeamSummary)
async def transfer_ownership(
    team_id: int, body: TeamTransfer,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """事务内转移唯一所有权，原所有者成为管理员。"""
    await management.transfer_ownership(db, team_id, user.id, body.user_id)
    await db.commit()
    return await queries.team_summary(db, team_id, user.id)


@router.get("/{team_id}/invitations", response_model=TeamPage[TeamInvitationOut])
async def list_invitations(
    team_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamInvitationOut]:
    """管理者分页查看邀请状态，列表不包含可使用的令牌。"""
    return await invitations.list_invitations(db, team_id, user.id, page, page_size)


@router.post("/{team_id}/invitations", response_model=TeamInvitationCreated, status_code=201)
async def create_invitation(
    team_id: int, body: TeamInviteCreate, request: Request,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationCreated:
    """签发七天邀请，提交后尝试邮件；失败仍返回可复制链接。"""
    team, invite, token = await invitations.create_invitation(db, team_id, user.id, str(body.email))
    await db.commit()
    return await invitations.invitation_result(team, invite, token, frontend_base=invitations.invitation_frontend_base(request.headers.get("origin")))


@router.post("/{team_id}/invitations/{invite_id}/revoke")
async def revoke_invitation(
    team_id: int, invite_id: int,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """撤回未被消费的邀请。"""
    await invitations.revoke_invitation(db, team_id, user.id, invite_id)
    await db.commit()
    return {"ok": True}


@router.post("/{team_id}/invitations/{invite_id}/resend", response_model=TeamInvitationCreated)
async def resend_invitation(
    team_id: int, invite_id: int, request: Request,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationCreated:
    """重发时旧令牌立即失效，新令牌从本次起有效七天。"""
    team, invite, token = await invitations.resend_invitation(db, team_id, user.id, invite_id)
    await db.commit()
    return await invitations.invitation_result(team, invite, token, frontend_base=invitations.invitation_frontend_base(request.headers.get("origin")))


@router.get("/{team_id}/activities", response_model=TeamPage[TeamActivityOut])
async def list_activities(
    team_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamActivityOut]:
    """分页读取团队操作动态。"""
    return await queries.list_activities(db, team_id, user.id, page, page_size)


@invitation_router.post("/inspect", response_model=TeamInvitationInspect)
async def inspect_invitation(
    body: TeamInvitationToken,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationInspect:
    """邮箱匹配的登录用户检查邀请，不提前消费令牌。"""
    team, invite = await invitations.check_invitation(db, body.token, user)
    return TeamInvitationInspect(team_name=team.name, email=invite.email, expires_at=invite.expires_at)


@invitation_router.post("/accept", response_model=TeamSummary)
async def accept_invitation(
    body: TeamInvitationToken,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """一次性接受邮箱绑定邀请，并激活普通成员身份。"""
    team = await invitations.accept_invitation(db, body.token, user)
    await db.commit()
    return await queries.team_summary(db, team.id, user.id)
