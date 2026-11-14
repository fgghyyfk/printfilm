"""团队、成员、一次性邀请与审计动态的持久化模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Team(Base):
    """独立团队空间；owner_user_id 是所有权的权威来源。"""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(180), default="")
    color: Mapped[str] = mapped_column(String(7), default="#435c4b")
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    monthly_limit_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TeamMember(Base):
    """成员关系软失效，退出或重新加入不会删除历史预算归属。"""

    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_member"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_team_member_role"),
        CheckConstraint("monthly_limit_fen IS NULL OR monthly_limit_fen >= 0", name="ck_team_member_limit"),
        Index("uq_team_active_owner", "team_id", unique=True, postgresql_where=text("active = true AND role = 'owner'"), sqlite_where=text("active = 1 AND role = 'owner'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    monthly_limit_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamInvitation(Base):
    """仅保存高熵邀请令牌的 SHA-256；明文仅在签发响应和邮件中使用。"""

    __tablename__ = "team_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeamActivity(Base):
    """不随项目删除的团队操作记录；不包含邀请令牌或密钥。"""

    __tablename__ = "team_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("drama_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
