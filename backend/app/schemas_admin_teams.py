"""平台管理员团队与钱包契约，金额均以整数分传递。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas_common import PageMeta


class AdminTeamOut(BaseModel):
    """团队归属、规模及独立钱包的只读快照。"""
    id: int
    name: str
    description: str
    color: str
    owner_user_id: int
    owner_nickname: str
    owner_email: str
    member_count: int
    project_count: int
    balance_fen: int
    frozen_fen: int
    monthly_limit_fen: int | None
    billing_source: Literal["owner_personal"] = "owner_personal"
    month_reserved_fen: int
    month_spent_fen: int
    total_spent_fen: int
    period: str
    created_at: datetime


class AdminTeamPage(BaseModel):
    """服务端筛选后的团队分页。"""
    items: list[AdminTeamOut]
    meta: PageMeta


class AdminTeamMemberOut(BaseModel):
    """成员关系及选定自然月消费，包括已退出的历史成员。"""
    user_id: int
    nickname: str
    email: str
    role: Literal["owner", "admin", "member"]
    active: bool
    monthly_limit_fen: int | None
    spent_fen: int
    reserved_fen: int
    joined_at: datetime


class AdminTeamMemberPage(BaseModel):
    """成员分页与消费统计月份。"""
    items: list[AdminTeamMemberOut]
    meta: PageMeta
    period: str


class AdminTeamUsageOut(BaseModel):
    """团队模型调用明细；明确区分已结算与待结算。"""
    id: int
    user_id: int
    user_nickname: str
    user_email: str
    project_id: int | None
    project_title: str | None
    task_run_id: int | None
    billing_key: str
    billing_label: str
    model: str
    total_tokens: int
    charge_fen: int
    settled: bool
    created_at: datetime


class AdminTeamUsageSummary(BaseModel):
    """全部筛选结果的统计，排除待结算金额，不受当前页影响。"""
    record_count: int
    settled_charge_fen: int


class AdminTeamUsagePage(BaseModel):
    """消费分页与相同筛选条件下的合计。"""
    items: list[AdminTeamUsageOut]
    meta: PageMeta
    summary: AdminTeamUsageSummary


class AdminTeamBalanceAdjustmentInput(BaseModel):
    """按增减金额调账；请求标识用于安全重试，不接受零金额或隐式类型转换。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    delta_fen: int = Field(strict=True, ge=-100_000_000, le=100_000_000)
    note: str = Field(min_length=1, max_length=180)
    request_id: UUID

    @model_validator(mode="after")
    def nonzero_amount(self) -> "AdminTeamBalanceAdjustmentInput":
        """零金额不能产生无意义的财务流水。"""
        if self.delta_fen == 0:
            raise ValueError("调账金额不能为零")
        return self


class AdminTeamBalanceAdjustmentOut(BaseModel):
    """原调账流水与当前可用余额；重试不会重复记账。"""
    ledger_id: int
    delta_fen: int
    balance_after_fen: int
    balance_fen: int
    frozen_fen: int
    replayed: bool


class AdminTeamWalletLedgerOut(BaseModel):
    """独立团队钱包流水，明确记录平台调账操作人。"""
    id: int
    actor_user_id: int
    operator_nickname: str
    operator_email: str
    delta_fen: int
    balance_after_fen: int
    kind: str
    ref_type: str
    ref_id: str
    note: str
    created_at: datetime


class AdminTeamWalletLedgerPage(BaseModel):
    """钱包流水服务端分页。"""
    items: list[AdminTeamWalletLedgerOut]
    meta: PageMeta


class AdminTeamBudgetInput(BaseModel):
    """空值不限额，0 停止新消费；严格整数分。"""
    model_config = ConfigDict(extra="forbid")
    monthly_limit_fen: int | None = Field(strict=True, ge=0, le=100_000_000)
