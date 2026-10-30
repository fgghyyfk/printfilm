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


@router.get(
    "",
    response_model=TeamPage[TeamSummary],
    summary="分页查询我的团队",
    description="需要登录，分页返回当前用户有效加入的团队摘要。",
    response_description="团队摘要及分页信息",
)
async def list_teams(
    page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamSummary]:
    """需要登录，分页返回当前用户有效加入的团队摘要。"""
    return await queries.list_teams(db, user.id, page, page_size)


@router.post(
    "",
    response_model=TeamSummary,
    status_code=201,
    summary="创建团队",
    description=(
        "需要登录，创建团队并将当前用户设为唯一所有者。团队生成费用由所有者个人余额承担，返回 201 并在 Location 响应头提供团队地址。"
    ),
    response_description="新建团队摘要",
)
async def create_team(
    body: TeamCreate, response: Response,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """需要登录，创建团队并将当前用户设为唯一所有者。团队生成费用由所有者个人余额承担，返回 201 并在 Location 响应头提供团队地址。"""
    team = await management.create_team(db, user.id, body)
    await db.commit()
    response.headers["Location"] = f"/api/teams/{team.id}"
    return await queries.team_summary(db, team.id, user.id)


@router.get(
    "/{team_id}",
    response_model=TeamSummary,
    summary="获取团队摘要",
    description="需要登录且为团队有效成员，返回可访问团队的基本资料、当前成员角色及成员、项目、资产数量。",
    response_description="团队摘要",
)
async def get_team_summary(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """需要登录且为团队有效成员，返回可访问团队的基本资料、当前成员角色及成员、项目、资产数量。"""
    return await queries.team_summary(db, team_id, user.id)


@router.patch(
    "/{team_id}",
    response_model=TeamSummary,
    summary="修改团队设置",
    description="需要登录且为团队所有者，修改团队基本资料或月度消费上限。",
    response_description="更新后的团队摘要",
)
async def update_team(
    team_id: int, body: TeamUpdate,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """需要登录且为团队所有者，修改团队基本资料或月度消费上限。"""
    await management.update_team(db, team_id, user.id, body)
    await db.commit()
    return await queries.team_summary(db, team_id, user.id)


@router.delete(
    "/{team_id}",
    status_code=204,
    summary="解散团队",
    description=(
        "需要登录且为团队所有者。存在未结束生成任务时返回 409；"
        "解散后撤销成员和邀请访问并释放编辑锁，历史内容及账单保留归档，成功返回 204。"
    ),
    response_description="解散成功，无响应正文",
)
async def delete_team(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> Response:
    """需要登录且为团队所有者。存在未结束生成任务时返回 409；解散后撤销成员和邀请访问并释放编辑锁，历史内容及账单保留归档，成功返回 204。"""
    await management.delete_team(db, team_id, user.id)
    await db.commit()
    return Response(status_code=204)


@router.get(
    "/{team_id}/members",
    response_model=TeamPage[TeamMemberOut],
    summary="分页查询团队成员",
    description="需要登录且为团队成员，支持按关键词和角色筛选有效成员；财务字段按查看者权限展示。",
    response_description="团队成员及分页信息",
)
async def list_members(
    team_id: int, q: str = Query("", max_length=254, description="按成员昵称或邮箱搜索的关键词"), role: Literal["owner", "admin", "member"] | None = None,
    page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamMemberOut]:
    """需要登录且为团队成员，支持按关键词和角色筛选有效成员；财务字段按查看者权限展示。"""
    return await queries.list_members(db, team_id, user.id, page, page_size, q, role)


@router.patch(
    "/{team_id}/members/{user_id}",
    response_model=TeamMemberOut,
    summary="调整团队成员角色和额度",
    description="需要登录且为团队所有者，修改成员月度额度或非所有者角色。不能通过本接口修改所有者角色，调整不会清空历史消费。",
    response_description="更新后的成员资料",
)
async def update_member(
    team_id: int, user_id: int, body: TeamMemberPatch,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamMemberOut:
    """需要登录且为团队所有者，修改成员月度额度或非所有者角色。不能通过本接口修改所有者角色，调整不会清空历史消费。"""
    await management.update_member(db, team_id, user.id, user_id, body)
    await db.commit()
    team, viewer = await get_team(db, team_id, user.id)
    member = await management.active_member(db, team_id, user_id)
    target = await db.get(User, user_id)
    return (await queries.member_outputs(db, team, viewer, [(member, target)]))[0]


@router.delete(
    "/{team_id}/members/{user_id}",
    summary="移除团队成员",
    description="需要登录且为所有者或管理员，管理员仅能移除普通成员。不能移除本人或所有者；成员软退出时清理尚未开始的任务和编辑锁，历史数据保留。",
    response_description="成员移除结果",
)
async def remove_member(
    team_id: int, user_id: int,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """需要登录且为所有者或管理员，管理员仅能移除普通成员。不能移除本人或所有者；成员软退出时清理尚未开始的任务和编辑锁，历史数据保留。"""
    await management.remove_member(db, team_id, user.id, user_id)
    await db.commit()
    return {"ok": True}


@router.post(
    "/{team_id}/leave",
    summary="退出团队",
    description="需要登录，当前成员退出团队并释放编辑锁、清理尚未开始的任务，历史内容和消费保留。所有者必须先转让所有权。",
    response_description="退出结果",
)
async def leave_team(
    team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """需要登录，当前成员退出团队并释放编辑锁、清理尚未开始的任务，历史内容和消费保留。所有者必须先转让所有权。"""
    await management.leave_team(db, team_id, user.id)
    await db.commit()
    return {"ok": True}


@router.post(
    "/{team_id}/transfer-ownership",
    response_model=TeamSummary,
    summary="转让团队所有权",
    description="需要登录且为团队所有者，将所有权转给另一位有效团队成员，原所有者转为管理员。之后团队费用由新所有者个人余额承担，历史消费归属不变。",
    response_description="转让后的团队摘要",
)
async def transfer_ownership(
    team_id: int, body: TeamTransfer,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """需要登录且为团队所有者，将所有权转给另一位有效团队成员，原所有者转为管理员。之后团队费用由新所有者个人余额承担，历史消费归属不变。"""
    await management.transfer_ownership(db, team_id, user.id, body.user_id)
    await db.commit()
    return await queries.team_summary(db, team_id, user.id)


@router.get(
    "/{team_id}/invitations",
    response_model=TeamPage[TeamInvitationOut],
    summary="分页查询团队邀请",
    description="需要登录且具有邀请管理权限，分页查看团队邀请状态；列表不返回可用邀请令牌。",
    response_description="邀请记录及分页信息",
)
async def list_invitations(
    team_id: int, page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamInvitationOut]:
    """需要登录且具有邀请管理权限，分页查看团队邀请状态；列表不返回可用邀请令牌。"""
    return await invitations.list_invitations(db, team_id, user.id, page, page_size)


@router.post(
    "/{team_id}/invitations",
    response_model=TeamInvitationCreated,
    status_code=201,
    summary="邀请成员加入团队",
    description="需要登录且具有邀请管理权限，按邮箱签发有效期为 7 天的邀请。提交后尝试发送邮件；发送失败仍返回可复制邀请链接。",
    response_description="邀请链接及邮件发送结果",
)
async def create_invitation(
    team_id: int, body: TeamInviteCreate, request: Request,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationCreated:
    """需要登录且具有邀请管理权限，按邮箱签发有效期为 7 天的邀请。提交后尝试发送邮件；发送失败仍返回可复制邀请链接。"""
    team, invite, token = await invitations.create_invitation(db, team_id, user.id, str(body.email))
    await db.commit()
    return await invitations.invitation_result(team, invite, token, frontend_base=invitations.invitation_frontend_base(request.headers.get("origin")))


@router.post(
    "/{team_id}/invitations/{invite_id}/revoke",
    summary="撤回团队邀请",
    description="需要登录且具有邀请管理权限，撤销尚未被接受的邀请，使其不能再用于加入团队。",
    response_description="邀请撤回结果",
)
async def revoke_invitation(
    team_id: int, invite_id: int,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """需要登录且具有邀请管理权限，撤销尚未被接受的邀请，使其不能再用于加入团队。"""
    await invitations.revoke_invitation(db, team_id, user.id, invite_id)
    await db.commit()
    return {"ok": True}


@router.post(
    "/{team_id}/invitations/{invite_id}/resend",
    response_model=TeamInvitationCreated,
    summary="重新发送团队邀请",
    description="需要登录且具有邀请管理权限，旧令牌立即失效，新令牌有效期从本次起重新计算 7 天，并尝试发送邀请邮件。",
    response_description="新邀请链接及邮件发送结果",
)
async def resend_invitation(
    team_id: int, invite_id: int, request: Request,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationCreated:
    """需要登录且具有邀请管理权限，旧令牌立即失效，新令牌有效期从本次起重新计算 7 天，并尝试发送邀请邮件。"""
    team, invite, token = await invitations.resend_invitation(db, team_id, user.id, invite_id)
    await db.commit()
    return await invitations.invitation_result(team, invite, token, frontend_base=invitations.invitation_frontend_base(request.headers.get("origin")))


@router.get(
    "/{team_id}/activities",
    response_model=TeamPage[TeamActivityOut],
    summary="分页查询团队动态",
    description="需要登录且为团队有效成员，分页读取团队操作动态。",
    response_description="团队动态及分页信息",
)
async def list_activities(
    team_id: int, page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamPage[TeamActivityOut]:
    """需要登录且为团队有效成员，分页读取团队操作动态。"""
    return await queries.list_activities(db, team_id, user.id, page, page_size)


@invitation_router.post(
    "/inspect",
    response_model=TeamInvitationInspect,
    summary="检查团队邀请",
    description="需要登录且登录邮箱与邀请绑定邮箱匹配，检查令牌有效性并返回团队名称、邀请邮箱和过期时间，不提前消费邀请令牌。",
    response_description="有效邀请的信息",
)
async def inspect_invitation(
    body: TeamInvitationToken,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamInvitationInspect:
    """需要登录且登录邮箱与邀请绑定邮箱匹配，检查令牌有效性并返回团队名称、邀请邮箱和过期时间，不提前消费邀请令牌。"""
    team, invite = await invitations.check_invitation(db, body.token, user)
    return TeamInvitationInspect(team_name=team.name, email=invite.email, expires_at=invite.expires_at)


@invitation_router.post(
    "/accept",
    response_model=TeamSummary,
    summary="接受团队邀请",
    description="需要登录且登录邮箱与邀请绑定邮箱匹配，一次性消费有效令牌并激活普通成员身份。",
    response_description="加入后的团队摘要",
)
async def accept_invitation(
    body: TeamInvitationToken,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> TeamSummary:
    """需要登录且登录邮箱与邀请绑定邮箱匹配，一次性消费有效令牌并激活普通成员身份。"""
    team = await invitations.accept_invitation(db, body.token, user)
    await db.commit()
    return await queries.team_summary(db, team.id, user.id)
