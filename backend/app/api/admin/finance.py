# Admin finance daily ledger API
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models import User
from app.schemas import AdminFinanceDailyOut
from app.services.admin.finance import build_finance_daily_list

router = APIRouter(prefix="/finance", tags=["admin-finance"])


@router.get("/daily", response_model=AdminFinanceDailyOut,
    summary="查看每日财务汇总",
    description="管理员按日查看最近 days 天的本地扣费、Token 用量与调用次数；days 支持 1 至 90 天。",
    response_description="操作成功，返回结果详见响应结构",
)
async def admin_finance_daily(
    days: int = Query(30, ge=1, le=90),
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminFinanceDailyOut:
    """按日汇总本地扣费、token 与调用次数。"""
    raw = await build_finance_daily_list(db, days=days)
    return AdminFinanceDailyOut(**raw)
