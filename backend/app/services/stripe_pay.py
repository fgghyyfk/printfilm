# -*- coding: utf-8 -*-
"""Stripe Checkout 充值通道（支付宝 / 微信支付走 Stripe 托管收银台）。

参考 Promptoll 的实现：直接调 Stripe REST（httpx），webhook 用标准库 hmac 验签，
不引入 stripe SDK。

流程：
  create_checkout_session() → POST /v1/checkout/sessions (mode=payment)
                              返回 {id: cs_…, url: https://checkout.stripe.com/…}
  用户在 Stripe 托管页用支付宝 / 微信扫码付款
  Stripe → /api/billing/stripe/webhook（checkout.session.completed /
           checkout.session.async_payment_succeeded）→ 验签后入账
  webhook 迟到时，订单查询接口用 retrieve_session() 主动补单。

注意：账户需在 Stripe Dashboard 开通 Alipay / WeChat Pay 支付方式；
微信支付只支持 CNY 与账户本币（如香港账户 HKD），由 stripe_currency 控制。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.config import get_settings

STRIPE_API = "https://api.stripe.com/v1"
# 区分同一 Stripe 账户下其他产品的事件
PRODUCT_TAG = "kaikou"
# Stripe Checkout Session 最短有效期 30 分钟
CHECKOUT_EXPIRE_SECONDS = 1800
# 零小数位币种（金额不乘 100）
_ZERO_DECIMAL = {"jpy", "krw", "vnd", "clp", "isk"}

_METHOD_TYPES = {"alipay": "alipay", "wxpay": "wechat_pay"}


class StripeNotConfigured(RuntimeError):
    """未配置 Stripe 密钥。"""


def configured() -> bool:
    """是否已配置 Stripe Secret Key。"""
    return bool((get_settings().stripe_secret_key or "").strip())


def mode() -> str:
    """返回 live / test / unset，供后台展示当前密钥环境。"""
    key = (get_settings().stripe_secret_key or "").strip()
    if key.startswith(("sk_live", "rk_live")):
        return "live"
    if key.startswith(("sk_test", "rk_test")):
        return "test"
    return "unset"


def presentment(amount_fen: int) -> tuple[int, str]:
    """把订单人民币金额（分）换算为 Stripe 展示金额（最小单位）与币种。

    stripe_currency=cny 时原样返回；其他币种按 stripe_fx_rate（1 CNY = x 目标币）折算。
    """
    s = get_settings()
    currency = (s.stripe_currency or "cny").strip().lower() or "cny"
    if currency == "cny":
        return int(amount_fen), "cny"
    rate = float(s.stripe_fx_rate or 0)
    if rate <= 0:
        raise ValueError("Stripe 汇率未配置（stripe_fx_rate 需大于 0）")
    major = amount_fen / 100 * rate
    if currency in _ZERO_DECIMAL:
        return int(round(major)), currency
    return int(round(major * 100)), currency


def _default_urls() -> tuple[str, str]:
    """支付成功 / 取消后的回跳地址，未配置时回到定价页。"""
    s = get_settings()
    base = (s.public_base_url or "").rstrip("/")
    success = (s.stripe_success_url or "").strip() or f"{base}/pricing?paid=1"
    cancel = (s.stripe_cancel_url or "").strip() or f"{base}/pricing"
    return success, cancel


def build_checkout_form(
    *,
    out_trade_no: str,
    name: str,
    amount_fen: int,
    pay_type: str,
    user_id: int,
) -> dict[str, str]:
    """组装 Checkout Session 表单（Stripe 用方括号嵌套键的 form 编码）。"""
    method = _METHOD_TYPES.get(pay_type)
    if not method:
        raise ValueError("pay_type 只支持 alipay / wxpay")
    unit_amount, currency = presentment(amount_fen)
    success, cancel = _default_urls()
    form: dict[str, str] = {
        "mode": "payment",
        "success_url": success,
        "cancel_url": cancel,
        "client_reference_id": out_trade_no,
        "expires_at": str(int(time.time()) + CHECKOUT_EXPIRE_SECONDS),
        "metadata[product]": PRODUCT_TAG,
        "metadata[out_trade_no]": out_trade_no,
        "metadata[user_id]": str(user_id),
        "metadata[amount_fen]": str(amount_fen),
        # 下单时的展示金额快照：汇率后续被改也能按原价核对
        "metadata[present_amount]": str(unit_amount),
        "metadata[present_currency]": currency,
        "payment_intent_data[metadata][product]": PRODUCT_TAG,
        "payment_intent_data[metadata][out_trade_no]": out_trade_no,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(unit_amount),
        "line_items[0][price_data][product_data][name]": name[:100] or "账户充值",
        "payment_method_types[0]": method,
        "locale": "zh",
    }
    if method == "wechat_pay":
        form["payment_method_options[wechat_pay][client]"] = "web"
    return form


async def create_checkout_session(
    *,
    out_trade_no: str,
    name: str,
    amount_fen: int,
    pay_type: str,
    user_id: int,
) -> dict[str, Any]:
    """创建一次性 Checkout Session，返回 {id, url, amount_total, currency}。"""
    if not configured():
        raise StripeNotConfigured("Stripe 未配置 Secret Key")
    form = build_checkout_form(
        out_trade_no=out_trade_no,
        name=name,
        amount_fen=amount_fen,
        pay_type=pay_type,
        user_id=user_id,
    )
    secret = get_settings().stripe_secret_key.strip()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{STRIPE_API}/checkout/sessions", data=form, auth=(secret, ""))
    if resp.status_code >= 400:
        try:
            msg = str(resp.json().get("error", {}).get("message") or "")
        except Exception:
            msg = ""
        raise ValueError(f"Stripe 下单失败：{msg[:160] or f'HTTP {resp.status_code}'}")
    body = resp.json()
    return {
        "id": str(body.get("id") or ""),
        "url": str(body.get("url") or ""),
        "amount_total": int(body.get("amount_total") or 0),
        "currency": str(body.get("currency") or ""),
    }


async def retrieve_session(session_id: str) -> dict[str, Any]:
    """查询 Checkout Session（webhook 迟到时的补单兜底）。"""
    if not configured():
        raise StripeNotConfigured("Stripe 未配置 Secret Key")
    secret = get_settings().stripe_secret_key.strip()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{STRIPE_API}/checkout/sessions/{session_id}", auth=(secret, ""))
        resp.raise_for_status()
        return resp.json()


def verify_webhook(payload: bytes, sig_header: str, *, tolerance_sec: int = 300) -> dict[str, Any] | None:
    """校验 Stripe-Signature 头，成功返回事件 dict，否则 None。

    头格式 "t=<ts>,v1=<sig>[,v1=…]"；签名串为 "<ts>.<原始 body>"，HMAC-SHA256。
    """
    secret = (get_settings().stripe_webhook_secret or "").strip()
    if not secret or not sig_header:
        return None
    ts: str | None = None
    v1s: list[str] = []
    for part in sig_header.split(","):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k == "t":
            ts = v
        elif k == "v1":
            v1s.append(v)
    if ts is None or not v1s:
        return None
    expected = hmac.new(secret.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in v1s):
        return None
    try:
        if tolerance_sec and abs(time.time() - int(ts)) > tolerance_sec:
            return None
        return json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return None


def session_paid_matches(sess: dict[str, Any], amount_fen: int) -> bool:
    """会话已支付，且实付金额/币种与下单快照一致、快照对应本订单人民币金额。"""
    if sess.get("payment_status") != "paid":
        return False
    meta = sess.get("metadata") or {}
    try:
        snap_fen = int(meta.get("amount_fen") or -1)
        snap_amount = int(meta.get("present_amount") or -1)
    except (TypeError, ValueError):
        return False
    snap_cur = str(meta.get("present_currency") or "").lower()
    return (
        snap_fen == int(amount_fen)
        and int(sess.get("amount_total") or 0) == snap_amount
        and str(sess.get("currency") or "").lower() == snap_cur
    )
