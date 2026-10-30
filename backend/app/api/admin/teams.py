"""平台管理员团队查询和可审计的钱包调账。"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models import User
from app.schemas_admin_teams import AdminTeamBudgetInput, AdminTeamMemberPage, AdminTeamOut, AdminTeamPage, AdminTeamUsagePage
from app.schemas_admin_teams import AdminTeamBalanceAdjustmentInput, AdminTeamBalanceAdjustmentOut, AdminTeamWalletLedgerPage
from app.services.teams import admin_queries, admin_wallet

router = APIRouter(prefix="/teams", dependencies=[Depends(get_current_admin)])


@router.get("", response_model=AdminTeamPage,
    summary="分页查询全站团队",
    description="管理员按团队或所有者搜索团队，返回基本资料、钱包及消费快照。",
    response_description="团队列表、钱包与消费快照以及分页信息",
)
async def list_teams(
    q: str = Query("", max_length=180),
    owner_id: int | None = Query(None, ge=1, le=2_147_483_647),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamPage:
    """按团队或所有者搜索并分页返回钱包与消费快照。"""
    return await admin_queries.list_teams(db, q=q, owner_id=owner_id, page=page, page_size=page_size)


@router.get("/{team_id}", response_model=AdminTeamOut,
    summary="查看团队详情",
    description="管理员查看指定团队的基本资料与消费信息。",
    response_description="操作成功，返回结果详见响应结构",
)
async def team_detail(
    team_id: int = Path(ge=1, le=2_147_483_647),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamOut:
    """查看指定团队基本资料。"""
    return await admin_queries.team_detail(db, team_id)


@router.get("/{team_id}/members", response_model=AdminTeamMemberPage,
    summary="分页查询团队成员",
    description="管理员查看团队有效或已退出成员，可按关键词、角色、成员状态和月份筛选；返回对应月份的消费、预留及额度。月份格式为 YYYY-MM。",
    response_description="操作成功，返回结果详见响应结构",
)
async def list_members(
    team_id: int = Path(ge=1, le=2_147_483_647),
    q: str = Query("", max_length=180),
    role: Literal["owner", "admin", "member"] | None = None,
    state: Literal["all", "active", "removed"] = "all",
    period: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamMemberPage:
    """查看有效或已退出成员及指定月份的实际消费、预留和额度。"""
    return await admin_queries.list_members(db, team_id, q=q, role=role, state=state, period=period, page=page, page_size=page_size)


@router.get("/{team_id}/usage-events", response_model=AdminTeamUsagePage,
    summary="分页查询团队消费",
    description="管理员按成员、模型或项目关键词、结算状态和日期范围查询团队消费。日期按 Asia/Shanghai 时区解释。",
    response_description="操作成功，返回结果详见响应结构",
)
async def list_usage(
    team_id: int = Path(ge=1, le=2_147_483_647),
    q: str = Query("", max_length=180),
    user_id: int | None = Query(None, ge=1, le=2_147_483_647),
    settled: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamUsagePage:
    """按成员、模型/项目关键词、结算状态和上海日期范围查看团队消费。"""
    return await admin_queries.list_usage(db, team_id, q=q, user_id=user_id, settled=settled, date_from=date_from, date_to=date_to, page=page, page_size=page_size)


@router.post("/{team_id}/balance-adjustments", response_model=AdminTeamBalanceAdjustmentOut,
    summary="团队钱包调账（已停用）", responses={410: {"description": "团队钱包调账已停用，请调整所有者个人余额"}},
    description="此兼容入口始终返回 410。团队已改为由所有者的个人账户付费，请通过用户管理接口调整团队所有者的个人余额。",
    response_description="兼容保留的响应结构；此入口实际返回 HTTP 410",
)
async def adjust_balance(
    body: AdminTeamBalanceAdjustmentInput,
    team_id: int = Path(ge=1, le=2_147_483_647),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamBalanceAdjustmentOut:
    """团队钱包调账已停用，请通过用户管理调整所有者个人余额。"""
    raise HTTPException(410, "团队已改为所有者个人账户付费，请在用户管理调整所有者余额")


@router.get("/{team_id}/wallet-ledger", response_model=AdminTeamWalletLedgerPage,
    summary="分页查询团队历史钱包流水",
    description="管理员查看团队历史钱包流水及后台调账审计记录；adjustments_only 可仅查看调账记录。",
    response_description="操作成功，返回结果详见响应结构",
)
async def wallet_ledger(
    team_id: int = Path(ge=1, le=2_147_483_647),
    q: str = Query("", max_length=180),
    adjustments_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminTeamWalletLedgerPage:
    """分页查看团队钱包流水及后台调账审计记录。"""
    return await admin_wallet.list_wallet_ledger(db, team_id, q=q, adjustments_only=adjustments_only, page=page, page_size=page_size)


@router.patch("/{team_id}/budget",
    summary="设置团队月度额度",
    description="管理员设置团队月总额度，金额单位为分，保留已有消费和在途任务。返回设置后的 monthly_limit_fen。",
    response_description="操作成功，返回结果详见响应结构",
)
async def update_budget(body: AdminTeamBudgetInput, team_id: int = Path(ge=1), admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """平台管理员设置团队月总额度，保留已有消费和在途任务。"""
    from app.services.teams.budgets import set_budget
    await set_budget(db, team_id, admin.id, body.monthly_limit_fen)
    await db.commit()
    return {"monthly_limit_fen": body.monthly_limit_fen}


@router.patch("/{team_id}/members/{user_id}/budget",
    summary="设置成员月度额度",
    description="管理员设置指定有效成员的月度额度，支持团队所有者，金额单位为分。返回设置后的 monthly_limit_fen。",
    response_description="操作成功，返回结果详见响应结构",
)
async def update_member_budget(body: AdminTeamBudgetInput, team_id: int = Path(ge=1), user_id: int = Path(ge=1), admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """平台管理员设置有效成员月额度，包含所有者。"""
    from app.services.teams.budgets import set_budget
    await set_budget(db, team_id, admin.id, body.monthly_limit_fen, user_id=user_id)
    await db.commit()
    return {"monthly_limit_fen": body.monthly_limit_fen}
