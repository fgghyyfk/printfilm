"""Stripe 充值通道：下单表单、验签、金额核对与 webhook 入账（不请求真实 Stripe）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.billing_stripe import stripe_webhook
from app.config import get_settings
from app.models import Order, User
from app.services import stripe_pay
from app.services.billing.settlement import close_expired_pending_orders

from tests.conftest import make_user

WHSEC = "whsec_test_secret"


@pytest.fixture
def stripe_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试用 Stripe 配置：CNY 原价展示。"""
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_webhook_secret", WHSEC)
    monkeypatch.setattr(s, "stripe_currency", "cny")
    monkeypatch.setattr(s, "stripe_fx_rate", 1.0)
    monkeypatch.setattr(s, "payment_provider", "stripe")


def _sign(payload: bytes, ts: int | None = None) -> str:
    """按 Stripe 规则生成 Stripe-Signature 头。"""
    t = ts or int(time.time())
    sig = hmac.new(WHSEC.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _paid_session(out_trade_no: str, amount_fen: int, **over) -> dict:
    """构造已支付的 Checkout Session 对象。"""
    sess = {
        "id": "cs_test_1",
        "payment_status": "paid",
        "payment_intent": "pi_test_1",
        "amount_total": amount_fen,
        "currency": "cny",
        "client_reference_id": out_trade_no,
        "metadata": {
            "product": stripe_pay.PRODUCT_TAG,
            "out_trade_no": out_trade_no,
            "amount_fen": str(amount_fen),
            "present_amount": str(amount_fen),
            "present_currency": "cny",
        },
    }
    sess.update(over)
    return sess


def _request(payload: bytes, sig: str) -> Request:
    """构造携带原始 body 的 Starlette 请求。"""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/billing/stripe/webhook",
        "headers": [(b"stripe-signature", sig.encode())],
    }
    sent = {"done": False}

    async def receive() -> dict:
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


def test_checkout_form_alipay_and_wechat(stripe_settings: None) -> None:
    ali = stripe_pay.build_checkout_form(out_trade_no="PF1", name="充值 100", amount_fen=10000, pay_type="alipay", user_id=7)
    assert ali["payment_method_types[0]"] == "alipay"
    assert ali["line_items[0][price_data][currency]"] == "cny"
    assert ali["line_items[0][price_data][unit_amount]"] == "10000"
    assert ali["metadata[out_trade_no]"] == "PF1"
    wx = stripe_pay.build_checkout_form(out_trade_no="PF2", name="充值", amount_fen=990, pay_type="wxpay", user_id=7)
    assert wx["payment_method_types[0]"] == "wechat_pay"
    assert wx["payment_method_options[wechat_pay][client]"] == "web"
    with pytest.raises(ValueError):
        stripe_pay.build_checkout_form(out_trade_no="PF3", name="x", amount_fen=1, pay_type="card", user_id=7)


def test_presentment_converts_currency(stripe_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_currency", "hkd")
    monkeypatch.setattr(s, "stripe_fx_rate", 1.08)
    assert stripe_pay.presentment(10000) == (10800, "hkd")
    monkeypatch.setattr(s, "stripe_fx_rate", 0)
    with pytest.raises(ValueError):
        stripe_pay.presentment(10000)


def test_verify_webhook_signature(stripe_settings: None) -> None:
    payload = b'{"type":"x"}'
    assert stripe_pay.verify_webhook(payload, _sign(payload)) == {"type": "x"}
    assert stripe_pay.verify_webhook(payload, _sign(b"tampered")) is None
    assert stripe_pay.verify_webhook(payload, _sign(payload, ts=int(time.time()) - 3600)) is None
    assert stripe_pay.verify_webhook(payload, "") is None


def test_session_paid_matches_snapshot(stripe_settings: None) -> None:
    assert stripe_pay.session_paid_matches(_paid_session("PF1", 1000), 1000)
    assert not stripe_pay.session_paid_matches(_paid_session("PF1", 1000, amount_total=1), 1000)
    assert not stripe_pay.session_paid_matches(_paid_session("PF1", 1000, currency="usd"), 1000)
    assert not stripe_pay.session_paid_matches(_paid_session("PF1", 1000), 2000)
    assert not stripe_pay.session_paid_matches(_paid_session("PF1", 1000, payment_status="unpaid"), 1000)


async def _make_order(db: AsyncSession, user: User, out_trade_no: str, **over) -> Order:
    """建一笔 Stripe 待支付订单。"""
    order = Order(
        out_trade_no=out_trade_no,
        user_id=user.id,
        sku_id="test",
        amount_fen=1000,
        credit_fen=1200,
        pay_type="wxpay",
        provider="stripe",
        external_id="cs_test_1",
        status="pending",
        **over,
    )
    db.add(order)
    await db.commit()
    return order


async def test_webhook_credits_once(db_session: AsyncSession, stripe_settings: None) -> None:
    user = await make_user(db_session, balance_fen=0)
    order = await _make_order(db_session, user, "PFSTRIPE1")
    event = {"type": "checkout.session.async_payment_succeeded", "data": {"object": _paid_session(order.out_trade_no, 1000)}}
    payload = json.dumps(event).encode()

    resp = await stripe_webhook(_request(payload, _sign(payload)), db_session)
    assert resp.status_code == 200
    resp = await stripe_webhook(_request(payload, _sign(payload)), db_session)
    assert resp.status_code == 200

    await db_session.refresh(user)
    await db_session.refresh(order)
    assert order.status == "paid"
    assert order.trade_no == "pi_test_1"
    assert int(user.balance_fen) == 1200


async def test_webhook_rejects_bad_sign_and_amount(db_session: AsyncSession, stripe_settings: None) -> None:
    user = await make_user(db_session, balance_fen=0)
    order = await _make_order(db_session, user, "PFSTRIPE2")
    event = {"type": "checkout.session.completed", "data": {"object": _paid_session(order.out_trade_no, 1000, amount_total=1)}}
    payload = json.dumps(event).encode()

    bad = await stripe_webhook(_request(payload, "t=1,v1=deadbeef"), db_session)
    assert bad.status_code == 400
    await stripe_webhook(_request(payload, _sign(payload)), db_session)

    await db_session.refresh(order)
    await db_session.refresh(user)
    assert order.status == "pending"
    assert int(user.balance_fen) == 0


async def test_stripe_order_expires_after_30_minutes(db_session: AsyncSession, stripe_settings: None) -> None:
    from datetime import datetime, timedelta, timezone

    user = await make_user(db_session, balance_fen=0)
    now = datetime.now(timezone.utc)
    young = await _make_order(db_session, user, "PFSTRIPE3", created_at=now - timedelta(minutes=10))
    old = await _make_order(db_session, user, "PFSTRIPE4", created_at=now - timedelta(minutes=31))
    await close_expired_pending_orders(db_session, user_id=user.id)
    rows = {
        o.out_trade_no: o.status
        for o in (await db_session.execute(select(Order).where(Order.user_id == user.id))).scalars()
    }
    assert rows[young.out_trade_no] == "pending"
    assert rows[old.out_trade_no] == "closed"


async def test_create_order_redirects_and_poll_syncs(
    db_session: AsyncSession, stripe_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe 通道下单返回托管页跳转；webhook 未到时查单主动补单入账。"""
    from app.api import billing as billing_api
    from app.services import billing

    sku = billing.SKUS[0]
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": "cs_test_poll", "url": "https://checkout.stripe.com/c/pay/cs_test_poll", "amount_total": 0, "currency": "cny"}

    monkeypatch.setattr(stripe_pay, "create_checkout_session", fake_create)
    user = await make_user(db_session, balance_fen=0)
    req = _request(b"", "")
    out = await billing_api.create_order(
        billing_api.CreateOrderBody(sku_id=sku["id"], pay_type="alipay"), req, db_session, user
    )
    assert out["provider"] == "stripe"
    assert out["pay_mode"] == "redirect"
    assert out["payurl"].startswith("https://checkout.stripe.com/")
    assert out["expire_seconds"] == stripe_pay.CHECKOUT_EXPIRE_SECONDS
    assert captured["pay_type"] == "alipay"

    amount = int(sku["amount_fen"])

    async def fake_retrieve(session_id: str) -> dict:
        assert session_id == "cs_test_poll"
        return _paid_session(out["out_trade_no"], amount, id=session_id, payment_intent="pi_poll")

    monkeypatch.setattr(stripe_pay, "retrieve_session", fake_retrieve)
    status = await billing_api.get_order(out["out_trade_no"], db_session, user)
    assert status["status"] == "paid"
    await db_session.refresh(user)
    assert int(user.balance_fen) == int(sku["credit_fen"])
