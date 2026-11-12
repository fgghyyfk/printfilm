# -*- coding: utf-8 -*-
"""充值订单公共逻辑：通道选择、订单有效期、入账（易支付 / Stripe 共用）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Order, User
from app.services.billing.pricing import ORDER_EXPIRE_SECONDS
from app.services.billing.settlement import credit_topup

logger = logging.getLogger(__name__)

PROVIDERS = ("epay", "stripe")


def active_provider() -> str:
    """当前生效的充值通道（后台「支付设置」可切换），非法值回落 epay。"""
    value = (get_settings().payment_provider or "epay").strip().lower()
    return value if value in PROVIDERS else "epay"


def order_expire_seconds(provider: str) -> int:
    """订单本地有效期：Stripe Checkout 最短 30 分钟，易支付 5 分钟。"""
    if provider == "stripe":
        from app.services.stripe_pay import CHECKOUT_EXPIRE_SECONDS

        return CHECKOUT_EXPIRE_SECONDS
    return ORDER_EXPIRE_SECONDS


async def lock_order(db: AsyncSession, out_trade_no: str) -> Order | None:
    """行锁读取订单，串行化同一订单的并发回调。"""
    result = await db.execute(
        select(Order).where(Order.out_trade_no == out_trade_no).with_for_update()
    )
    return result.scalar_one_or_none()


async def mark_order_paid(db: AsyncSession, order: Order, *, trade_no: str, note: str) -> bool:
    """订单置为已支付并给钱包入账（幂等）；调用方负责 commit。

    已关闭（本地超时）的订单若通道确认已付款，照常入账，避免用户付了钱拿不到额度。
    返回 True 表示本次新入账，False 表示此前已入账。
    """
    if order.status == "paid":
        return False
    user = await db.get(User, order.user_id)
    if not user:
        raise ValueError("订单用户不存在")
    order.status = "paid"
    order.trade_no = trade_no or None
    order.paid_at = datetime.now(timezone.utc)
    await credit_topup(
        db,
        user,
        int(order.credit_fen),
        ref_type="order",
        ref_id=order.out_trade_no,
        note=note,
    )
    logger.info(
        "topup paid order=%s provider=%s user=%s credit=%s",
        order.out_trade_no,
        order.provider,
        user.id,
        order.credit_fen,
    )
    return True
