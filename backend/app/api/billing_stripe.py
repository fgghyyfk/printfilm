# -*- coding: utf-8 -*-
"""Stripe webhook：支付宝 / 微信支付（Stripe Checkout）付款成功后入账。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import stripe_pay
from app.services.billing import topup_orders

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

# 卡支付走 completed；支付宝 / 微信是异步支付方式，最终以 async_payment_succeeded 为准
_PAID_EVENTS = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    """接收 Stripe 事件：对原始 body 验签，付款成功且金额一致则给订单入账（幂等）。

    返回 2xx 后 Stripe 停止重试；验签失败返回 400。
    """
    payload = await request.body()
    event = stripe_pay.verify_webhook(payload, request.headers.get("Stripe-Signature", ""))
    if event is None:
        logger.warning("stripe webhook bad signature")
        return PlainTextResponse("invalid signature", status_code=400)

    if event.get("type") not in _PAID_EVENTS:
        return PlainTextResponse("ok")
    sess = (event.get("data") or {}).get("object") or {}
    meta = sess.get("metadata") or {}
    # 同一 Stripe 账户下其他产品的事件直接忽略
    if meta.get("product") != stripe_pay.PRODUCT_TAG:
        return PlainTextResponse("ok")
    if sess.get("payment_status") != "paid":
        return PlainTextResponse("ok")  # 异步支付处理中，等 succeeded 事件

    out_trade_no = str(meta.get("out_trade_no") or sess.get("client_reference_id") or "")
    if not out_trade_no:
        return PlainTextResponse("ok")
    order = await topup_orders.lock_order(db, out_trade_no)
    if order is None:
        logger.warning("stripe webhook unknown order %s", out_trade_no)
        return PlainTextResponse("order not found", status_code=404)
    if order.status == "paid":
        return PlainTextResponse("ok")
    if not stripe_pay.session_paid_matches(sess, int(order.amount_fen)):
        logger.error(
            "stripe amount mismatch order=%s expect_fen=%s got=%s %s meta=%s",
            out_trade_no,
            order.amount_fen,
            sess.get("amount_total"),
            sess.get("currency"),
            meta,
        )
        # 返回 2xx 不让 Stripe 无限重试；金额异常需人工核对
        return PlainTextResponse("amount mismatch")

    pi = str(sess.get("payment_intent") or sess.get("id") or "")
    await topup_orders.mark_order_paid(db, order, trade_no=pi, note=f"stripe:{order.pay_type}")
    await db.commit()
    return PlainTextResponse("ok")
