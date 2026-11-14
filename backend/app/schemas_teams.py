"""团队核心 API 数据契约；所有金额使用整数分。"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.schemas_common import PageMeta

TeamRole = Literal["owner", "admin", "member"]
T = TypeVar("T")


class TeamInput(BaseModel):
    """输入统一去除首尾空格，拒绝未知字段。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class TeamCreate(TeamInput):
    """创建独立团队空间。"""

    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=180)
    color: str = Field(default="#435c4b", pattern=r"^#[0-9a-fA-F]{6}$")


class TeamUpdate(TeamInput):
    """部分修改团队资料；显式 null 不是有效资料值。"""

    name: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=180)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    monthly_limit_fen: int | None = Field(default=None, strict=True, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "TeamUpdate":
        """防止 null 擦除非空字段或空补丁写入无意义动态。"""
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set if key != "monthly_limit_fen"):
            raise ValueError("请提供有效的团队资料")
        return self


class TeamSummary(BaseModel):
    """当前用户可见的团队摘要。"""

    id: int
    name: str
    description: str
    color: str
    created_at: datetime
    updated_at: datetime
    my_role: TeamRole
    viewer_id: int
    member_count: int = 0
    project_count: int = 0
    asset_count: int = 0


class TeamPage(BaseModel, Generic[T]):
    """团队所有列表共用服务端分页形态。"""

    items: list[T]
    meta: PageMeta


class TeamMemberOut(BaseModel):
    """非所有者查看他人时，财务字段返回 null。"""

    id: int
    nickname: str
    email: str
    avatar_url: str
    role: TeamRole
    monthly_limit_fen: int | None
    spent_fen: int | None
    reserved_fen: int | None
    joined_at: datetime


class TeamMemberPatch(TeamInput):
    """仅所有者可修改非所有者角色和月度上限。"""

    role: Literal["admin", "member"] | None = None
    monthly_limit_fen: int | None = Field(default=None, strict=True, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "TeamMemberPatch":
        """null 额度表示不限额；null 角色和空补丁均无效。"""
        if not self.model_fields_set or ("role" in self.model_fields_set and self.role is None):
            raise ValueError("请提供有效的成员角色或额度")
        return self


class TeamTransfer(TeamInput):
    """所有权只能转移给仍在团队内的其他成员。"""

    user_id: int = Field(strict=True, gt=0)


class TeamInviteCreate(TeamInput):
    """邮箱绑定的普通成员邀请。"""

    email: EmailStr = Field(max_length=254)


class TeamInvitationOut(BaseModel):
    """邀请列表不暴露原始令牌或哈希。"""

    id: int
    email: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "accepted", "revoked", "expired"]


class TeamInvitationCreated(BaseModel):
    """签发或轮换后一次性返回明文供管理员复制。"""

    invitation: TeamInvitationOut
    token: str
    accept_url: str
    email_sent: bool


class TeamInvitationToken(TeamInput):
    """令牌置于 POST 正文，避免落入接口 URL 日志。"""

    token: str = Field(min_length=20, max_length=256)


class TeamInvitationInspect(BaseModel):
    """匹配邮箱的已登录用户可看到加入前必要信息。"""

    team_name: str
    email: str
    expires_at: datetime


class TeamActivityOut(BaseModel):
    """团队动态保留实际操作者，允许原项目已删除。"""

    id: int
    actor_user_id: int
    actor_nickname: str
    action: str
    subject: str
    project_id: int | None
    created_at: datetime
