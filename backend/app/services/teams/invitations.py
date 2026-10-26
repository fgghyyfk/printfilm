"""邮箱绑定、七天有效、一次性消费的团队邀请。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.models_teams import Team, TeamInvitation, TeamMember
from app.schemas_common import PageMeta
from app.schemas_teams import TeamInvitationCreated, TeamInvitationOut, TeamPage
from app.services.email import send_email
from app.services.teams.access import get_team
from app.services.teams.activity import record_activity


def hash_token(token: str) -> str:
    """仅高熵令牌的摘要允许进入数据库，避免泄漏可直接使用的邀请。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invitation_out(invite: TeamInvitation) -> TeamInvitationOut:
    """响应只返回邀请状态和必要信息，不包含令牌或哈希。"""
    status = "accepted" if invite.accepted_at else "revoked" if invite.revoked_at else "expired" if invite.expires_at <= datetime.now(UTC) else "pending"
    return TeamInvitationOut(id=invite.id, email=invite.email, created_at=invite.created_at, expires_at=invite.expires_at, status=status)


async def list_invitations(db: AsyncSession, team_id: int, user_id: int, page: int, page_size: int) -> TeamPage[TeamInvitationOut]:
    """只有所有者和管理员可读取邀请邮箱列表。"""
    await get_team(db, team_id, user_id, roles=("owner", "admin"))
    total = int(await db.scalar(select(func.count()).select_from(TeamInvitation).where(TeamInvitation.team_id == team_id)) or 0)
    rows = (await db.execute(select(TeamInvitation).where(TeamInvitation.team_id == team_id).order_by(TeamInvitation.created_at.desc(), TeamInvitation.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return TeamPage(items=[invitation_out(row) for row in rows], meta=PageMeta(page=page, page_size=page_size, total=total))


async def create_invitation(db: AsyncSession, team_id: int, user_id: int, email: str) -> tuple[Team, TeamInvitation, str]:
    """团队行锁串行化同邮箱签发，允许过期或撤回后重新邀请。"""
    team, _ = await get_team(db, team_id, user_id, roles=("owner", "admin"), lock=True)
    email = email.strip().lower()
    await ensure_invitable(db, team_id, email)
    token = secrets.token_urlsafe(32)
    invite = TeamInvitation(team_id=team_id, email=email, token_hash=hash_token(token), created_by=user_id, expires_at=datetime.now(UTC) + timedelta(days=7))
    db.add(invite)
    await record_activity(db, team_id, user_id, "invitation.created", email)
    return team, invite, token


async def ensure_invitable(db: AsyncSession, team_id: int, email: str, exclude_id: int | None = None) -> None:
    """签发与重发均排除已在团队者和同邮箱其他有效邀请。"""
    existing_member = await db.scalar(select(TeamMember.id).join(User, User.id == TeamMember.user_id).where(TeamMember.team_id == team_id, TeamMember.active.is_(True), func.lower(User.email) == email).limit(1))
    if existing_member:
        raise HTTPException(409, "该邮箱已是团队成员")
    stmt = select(TeamInvitation.id).where(TeamInvitation.team_id == team_id, TeamInvitation.email == email, TeamInvitation.accepted_at.is_(None), TeamInvitation.revoked_at.is_(None), TeamInvitation.expires_at > datetime.now(UTC))
    if exclude_id is not None:
        stmt = stmt.where(TeamInvitation.id != exclude_id)
    pending = await db.scalar(stmt.limit(1))
    if pending:
        raise HTTPException(409, "已存在有效邀请，请勿重复邀请")


async def managed_invitation(db: AsyncSession, team_id: int, user_id: int, invite_id: int) -> tuple[Team, TeamInvitation]:
    """管理写操作统一先锁团队，再锁所属邀请。"""
    team, _ = await get_team(db, team_id, user_id, roles=("owner", "admin"), lock=True)
    invite = (await db.execute(select(TeamInvitation).where(TeamInvitation.id == invite_id, TeamInvitation.team_id == team_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "邀请不存在")
    return team, invite


async def revoke_invitation(db: AsyncSession, team_id: int, user_id: int, invite_id: int) -> None:
    """撤回未消费邀请；重复撤回保持幂等。"""
    _, invite = await managed_invitation(db, team_id, user_id, invite_id)
    if invite.accepted_at:
        raise HTTPException(409, "邀请已被接受，不能撤回")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        await record_activity(db, team_id, user_id, "invitation.revoked", invite.email)


async def resend_invitation(db: AsyncSession, team_id: int, user_id: int, invite_id: int) -> tuple[Team, TeamInvitation, str]:
    """重发轮换哈希使旧链接立即失效；已接受或撤回邀请不能重发。"""
    team, invite = await managed_invitation(db, team_id, user_id, invite_id)
    if invite.accepted_at or invite.revoked_at:
        raise HTTPException(409, "邀请已结束，请重新创建邀请")
    await ensure_invitable(db, team_id, invite.email, exclude_id=invite.id)
    token = secrets.token_urlsafe(32)
    invite.token_hash = hash_token(token)
    invite.expires_at = datetime.now(UTC) + timedelta(days=7)
    await record_activity(db, team_id, user_id, "invitation.resent", invite.email)
    return team, invite, token


def invitation_frontend_base(origin: str | None) -> str:
    """只信任已配置 CORS 前端源，修正本地前后端分端口邀请链接。"""
    settings = get_settings()
    base = str(settings.public_base_url).rstrip("/")
    candidate = (origin or "").rstrip("/")
    allowed = {item.strip().rstrip("/") for item in str(settings.cors_origins or "").split(",")}
    if candidate not in allowed:
        return base
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return base
    if parts.scheme in {"http", "https"} and parts.netloc and not (parts.username or parts.password or parts.path or parts.query or parts.fragment):
        return candidate
    return base


async def invitation_result(team: Team, invite: TeamInvitation, token: str, *, frontend_base: str | None = None) -> TeamInvitationCreated:
    """提交签发事务之后尝试 SMTP，失败时仍允许复制有效邀请链接。"""
    base = frontend_base or str(get_settings().public_base_url).rstrip("/")
    link = f"{base}/teams/join?token={quote(token, safe='')}"
    sent = await send_email(to_addrs=[invite.email], subject=f"PRINTFILM 团队邀请：{team.name}", body=f"您受邀加入「{team.name}」。请使用 {invite.email} 登录，并在七天内打开以下链接：\n\n{link}\n\n如非本人操作，请忽略本邮件。")
    return TeamInvitationCreated(invitation=invitation_out(invite), token=token, accept_url=link, email_sent=sent)


async def check_invitation(db: AsyncSession, token: str, user: User, *, lock: bool = False) -> tuple[Team, TeamInvitation]:
    """登录邮箱必须匹配；接受时按团队→邀请顺序加锁并重新核对令牌。"""
    digest = hash_token(token)
    scope_id = await db.scalar(select(TeamInvitation.team_id).where(TeamInvitation.token_hash == digest))
    if scope_id is None:
        raise HTTPException(404, "邀请链接无效")
    team_stmt = select(Team).where(Team.id == scope_id)
    if lock:
        team_stmt = team_stmt.with_for_update()
    team = (await db.execute(team_stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    stmt = select(TeamInvitation).where(TeamInvitation.token_hash == digest, TeamInvitation.team_id == scope_id)
    if lock:
        stmt = stmt.with_for_update()
    invite = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    if not team or not invite:
        raise HTTPException(404, "邀请链接无效")
    if user.email.strip().lower() != invite.email:
        raise HTTPException(403, "请使用受邀邮箱登录")
    if invite.accepted_at or invite.revoked_at:
        raise HTTPException(409, "邀请已被使用或撤回")
    if invite.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "邀请已过期，请联系管理员重新邀请")
    return team, invite


async def accept_invitation(db: AsyncSession, token: str, user: User) -> Team:
    """一次性加入或激活历史成员，保留月额度及原月消费账本。"""
    team, invite = await check_invitation(db, token, user, lock=True)
    member = (await db.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if member and member.active:
        raise HTTPException(409, "你已是团队成员")
    if member:
        member.active, member.role = True, "member"
    else:
        db.add(TeamMember(team_id=team.id, user_id=user.id, role="member", active=True))
    invite.accepted_at = datetime.now(UTC)
    await record_activity(db, team.id, user.id, "member.joined", user.nickname)
    return team
