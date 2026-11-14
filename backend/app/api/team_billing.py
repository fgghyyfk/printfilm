"""所有者统一付款的团队用量；旧钱包、旧订单只读兼容。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.billing import CreateOrderBody
from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Order, UsageEvent, User
from app.models_drama import DramaProject
from app.models_tasks import TaskRun
from app.models_team_billing import TeamMonthlyUsage, TeamWalletLedger
from app.services import billing
from app.services.billing.team import billing_period
from app.services.teams.access import get_team

router = APIRouter(prefix="/teams/{team_id}", tags=["team-billing"])


def order_out(order: Order) -> dict:
    sku = billing.sku_by_id(order.sku_id) or {}
    return {"out_trade_no": order.out_trade_no, "sku_id": order.sku_id, "sku_name": str(sku.get("name") or order.sku_id), "amount_fen": order.amount_fen, "credit_fen": order.credit_fen, "pay_type": order.pay_type, "status": order.status, "trade_no": order.trade_no, "paid_at": order.paid_at.isoformat() if order.paid_at else None, "created_at": order.created_at.isoformat() if order.created_at else None}


@router.get(
    "/wallet",
    summary="获取团队付款账户余额",
    description=(
        "需要登录且为团队所有者，返回所有者个人钱包的余额、冻结金额及计费开关，billing_source 为 owner_personal。"
    ),
    response_description="团队所有者付款账户余额",
)
async def wallet(team_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await get_team(db, team_id, user.id, roles={"owner"})
    row = await db.get(User, user.id)
    if row is None:
        raise HTTPException(404, "团队钱包不存在")
    return {"balance_fen": int(row.balance_fen or 0), "frozen_fen": int(row.frozen_fen or 0), "balance_yuan": round(int(row.balance_fen or 0) / 100, 2), "frozen_yuan": round(int(row.frozen_fen or 0) / 100, 2), "billing_enabled": bool(get_settings().billing_enabled), "billing_source": "owner_personal"}


@router.get(
    "/wallet/ledger",
    summary="分页查询历史团队钱包流水",
    description="需要登录且为团队所有者，按编号倒序分页读取历史团队钱包流水。当前团队费用由所有者个人余额支付，此接口保留旧流水查询能力。",
    response_description="历史团队钱包流水及分页信息",
)
async def wallet_ledger(team_id: int, page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await get_team(db, team_id, user.id, roles={"owner"})
    filters = [TeamWalletLedger.team_id == team_id]
    total = int((await db.execute(select(func.count()).select_from(TeamWalletLedger).where(*filters))).scalar_one() or 0)
    rows = (await db.execute(select(TeamWalletLedger).where(*filters).order_by(TeamWalletLedger.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [{"id": r.id, "member_id": r.actor_user_id, "delta_fen": r.delta_fen, "balance_after_fen": r.balance_after_fen, "kind": r.kind, "note": r.note, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows], "meta": {"page": page, "page_size": page_size, "total": total}}


@router.get(
    "/usage/summary",
    summary="获取团队月度用量汇总",
    description=(
        "需要登录且为团队成员，按月份汇总已消费和预留金额。"
        "所有者查看全队汇总及付款账户余额，其他成员仅查看本人用量和月度限额；"
        "金额单位为分。"
    ),
    response_description="按权限展示的月度用量",
)
async def usage_summary(team_id: int, period: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="消费月份，格式为 YYYY-MM；汇总接口不传时使用当前计费月"), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    team, member = await get_team(db, team_id, user.id)
    period = period or billing_period()
    filters = [TeamMonthlyUsage.team_id == team_id, TeamMonthlyUsage.period == period]
    owner = team.owner_user_id == user.id
    if not owner:
        filters.append(TeamMonthlyUsage.user_id == user.id)
    spent, reserved = (await db.execute(select(func.coalesce(func.sum(TeamMonthlyUsage.spent_fen), 0), func.coalesce(func.sum(TeamMonthlyUsage.reserved_fen), 0)).where(*filters))).one()
    data = {"spent_fen": int(spent), "reserved_fen": int(reserved), "monthly_limit_fen": member.monthly_limit_fen, "period": period, "billing_source": "owner_personal", "billing_enabled": bool(get_settings().billing_enabled)}
    if owner:
        row = await db.get(User, user.id)
        data.update(balance_fen=int(row.balance_fen or 0) if row else 0, frozen_fen=int(row.frozen_fen or 0) if row else 0, team_monthly_limit_fen=team.monthly_limit_fen)
    return data


@router.get(
    "/usage/records",
    summary="分页查询团队消费记录",
    description="需要登录且为团队成员，支持按关键词、成员、项目及月份筛选。所有者可查看全队记录，其他成员只能查看本人的消费；金额单位为分。",
    response_description="消费明细及分页信息",
)
async def usage_records(team_id: int, page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(20, ge=1, le=100, description="每页返回的记录数"), q: str | None = Query(None, max_length=100, description="按模型、计费项或项目标题搜索的关键词"), member_id: int | None = Query(None, ge=1, description="成员用户编号；非所有者只能查询本人"), project_id: int | None = Query(None, ge=1, description="团队漫剧项目编号"), period: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="消费月份，格式为 YYYY-MM；汇总接口不传时使用当前计费月"), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    team, member = await get_team(db, team_id, user.id)
    filters = [UsageEvent.team_id == team_id]
    if team.owner_user_id != user.id:
        if member_id is not None and member_id != user.id:
            raise HTTPException(403, "只能查看本人的消费记录")
        member_id = user.id
    if member_id is not None:
        filters.append(UsageEvent.user_id == member_id)
    if project_id is not None:
        filters.append(UsageEvent.drama_project_id == project_id)
    if period:
        filters.append(TaskRun.billing_period == period)
    if q:
        text = f"%{q}%"
        filters.append(or_(UsageEvent.model.ilike(text), UsageEvent.billing_key.ilike(text), DramaProject.title.ilike(text)))
    base = select(UsageEvent, DramaProject.title).outerjoin(TaskRun, UsageEvent.task_run_id == TaskRun.id).outerjoin(DramaProject, UsageEvent.drama_project_id == DramaProject.id).where(*filters)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
    rows = (await db.execute(base.order_by(UsageEvent.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [{"id": r.id, "member_id": r.user_id, "project_id": r.drama_project_id, "project_title": title, "label": billing.billing_key_label(r.billing_key), "billing_key": r.billing_key, "model": r.model or "", "amount_fen": int(r.charge_fen or 0), "total_tokens": int(r.total_tokens or 0), "created_at": r.created_at.isoformat() if r.created_at else None} for r, title in rows], "meta": {"page": page, "page_size": page_size, "total": total}}


@router.get(
    "/orders",
    summary="查询历史团队充值订单",
    description="需要登录且为团队所有者，按编号倒序查询历史团队充值订单，查询前自动关闭过期待支付订单。",
    response_description="历史团队充值订单列表",
)
async def list_orders(team_id: int, limit: int = Query(50, ge=1, le=100, description="最多返回的记录数"), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await get_team(db, team_id, user.id, roles={"owner"})
    await billing.close_expired_pending_orders(db, team_id=team_id)
    rows = (await db.execute(select(Order).where(Order.team_id == team_id).order_by(Order.id.desc()).limit(limit))).scalars().all()
    return {"orders": [order_out(row) for row in rows]}


@router.post(
    "/orders",
    summary="访问已停用的团队充值入口",
    description="需要登录且为团队所有者。团队独立充值已停用，始终返回 410，需改为给所有者个人账户充值以支付团队费用。",
    response_description="接口已停用，返回 410",
)
async def create_order(team_id: int, body: CreateOrderBody, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await get_team(db, team_id, user.id, roles={"owner"})
    raise HTTPException(410, "团队费用由所有者个人余额支付，请在个人账户充值")


async def owner_order(db, team_id: int, no: str, user: User) -> Order:
    await get_team(db, team_id, user.id, roles={"owner"})
    order = (await db.execute(select(Order).where(Order.team_id == team_id, Order.out_trade_no == no).with_for_update())).scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "订单不存在")
    return order


@router.get(
    "/orders/{out_trade_no}",
    summary="获取历史团队充值订单",
    description="需要登录且为团队所有者，读取本团队历史充值订单，查询前关闭该订单的过期待支付状态。",
    response_description="历史团队订单详情",
)
async def get_order(team_id: int, out_trade_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await get_team(db, team_id, user.id, roles={"owner"})
    await billing.close_expired_pending_orders(db, team_id=team_id, out_trade_no=out_trade_no)
    return order_out(await owner_order(db, team_id, out_trade_no, user))


@router.post(
    "/orders/{out_trade_no}/close",
    summary="关闭历史团队充值订单",
    description="需要登录且为团队所有者，关闭本团队的历史未支付订单；已支付订单返回 400，不存在返回 404。",
    response_description="关闭后的历史团队订单状态",
)
async def close_order(team_id: int, out_trade_no: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    order = await owner_order(db, team_id, out_trade_no, user)
    if order.status == "paid":
        raise HTTPException(400, "已支付订单无法关闭")
    order.status = "closed"
    await db.commit()
    return {"out_trade_no": out_trade_no, "status": "closed"}
