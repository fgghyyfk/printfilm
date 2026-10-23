"""市场平台投放订单、永久权益和收益账本。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.config import get_settings
from app.services import epay
from . import storage
from .dependencies import Actor, DB, Visitor, storage_ready
from .models import Asset, ContentOrder, Entitlement, Publication, RevenueConfig, RevenueLedger, SeriesEpisode, Work
from .schemas import ContentOrderInput
from .service import data, fail, now

router = APIRouter()


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "127.0.0.1")).split(",")[0].strip()


def _payment_ready() -> bool:
    settings = get_settings()
    return bool(settings.epay_pid and settings.epay_key and settings.epay_api_url)


def _publication_public(item: Publication) -> bool:
    return bool(item.audit_status == "approved" and item.listed and item.sale_type == "platform_unlock" and item.unlock_price_fen > 0 and item.playback_manifest_key)


async def _signed_hls_manifest(publication: Publication) -> str:
    """重写私有 HLS 分片为短时签名 URL，避免仅签 manifest 后分片 403。"""
    if not publication.playback_manifest_key:
        fail("MARKETPLACE_EPISODE_NOT_READY", 409)
    raw = await __import__("asyncio").to_thread(storage.read, publication.playback_manifest_key)
    text = raw.decode("utf-8")
    prefix = publication.playback_manifest_key.rsplit("/", 1)[0] + "/"
    rewritten: list[str] = []
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.startswith(("http://", "https://")):
            rewritten.append(line)
            continue
        # 只允许 Worker 生成目录中的相对分片，拒绝路径穿越或外部 URI。
        if value.startswith("/") or ".." in value.split("/"):
            fail("播放清单无效", 502)
        key = prefix + value
        signed = await __import__("asyncio").to_thread(storage.signed, key, "video/mp2t")
        rewritten.append(signed)
    return "\n".join(rewritten) + "\n"


@router.get("/platform/feed", response_model=dict)
async def platform_feed(db: DB, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    filters = (
        Publication.audit_status == "approved",
        Publication.listed.is_(True),
        Publication.sale_type == "platform_unlock",
        Publication.playback_status == "done",
        Publication.playback_manifest_key.is_not(None),
    )
    query = select(Publication).where(*filters).order_by(Publication.id.desc())
    rows = list((await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all())
    total = (await db.execute(select(func.count()).select_from(Publication).where(*filters))).scalar_one()
    return {"items": [data(row, "id", "content_type", "content_id", "version", "sale_type", "copyright_price_fen", "unlock_price_fen", "share_bps", "created_at") for row in rows], "meta": {"page": page, "page_size": page_size, "total": total}}


@router.get("/platform/episodes/{episode_id}", response_model=dict)
async def platform_episode(episode_id: int, db: DB, user: Visitor):
    episode = await db.get(SeriesEpisode, episode_id)
    if not episode:
        fail("分集不存在", 404)
    publication = (await db.execute(select(Publication).where(
        Publication.content_type == "series_episode", Publication.content_id == episode.id,
        Publication.version == episode.version, Publication.audit_status == "approved",
        Publication.listed.is_(True), Publication.sale_type == "platform_unlock",
    ))).scalar_one_or_none()
    if not publication or publication.sale_type != "platform_unlock":
        fail("分集尚未公开", 404)
    entitled = bool(user and (await db.execute(select(Entitlement.id).where(Entitlement.user_id == user.id, Entitlement.publication_id == publication.id, Entitlement.active.is_(True)))).scalar_one_or_none())
    return {**data(publication, "id", "content_type", "content_id", "version", "sale_type", "copyright_price_fen", "unlock_price_fen", "created_at"), "entitled": entitled, "playback_ready": bool(publication.playback_manifest_key)}


@router.get("/platform/works/{work_id}", response_model=dict)
async def platform_work(work_id: int, db: DB, user: Visitor):
    """单视频平台投放详情；与剧集分集使用相同的发布版本和永久权益模型。"""
    work = await db.get(Work, work_id)
    if not work:
        fail("作品不存在", 404)
    publication = (await db.execute(select(Publication).where(
        Publication.content_type == "work", Publication.content_id == work.id,
        Publication.version == work.version, Publication.audit_status == "approved",
        Publication.listed.is_(True), Publication.sale_type == "platform_unlock"))).scalar_one_or_none()
    if not publication or publication.sale_type != "platform_unlock":
        fail("作品尚未公开", 404)
    entitled = bool(user and (await db.execute(select(Entitlement.id).where(
        Entitlement.user_id == user.id, Entitlement.publication_id == publication.id,
        Entitlement.active.is_(True)))).scalar_one_or_none())
    return {**data(publication, "id", "content_type", "content_id", "version", "sale_type", "copyright_price_fen", "unlock_price_fen", "created_at"), "entitled": entitled, "playback_ready": bool(publication.playback_manifest_key)}


@router.post("/content-orders", response_model=dict)
async def create_content_order(body: ContentOrderInput, request: Request, db: DB, user: Actor):
    if not _payment_ready():
        fail("MARKETPLACE_PAYMENT_UNAVAILABLE", 503)
    publication = await db.get(Publication, body.publication_id)
    if not publication or not _publication_public(publication):
        fail("内容尚未开放购买", 409)
    existing = (await db.execute(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.publication_id == publication.id, Entitlement.active.is_(True)))).scalar_one_or_none()
    if existing:
        return {"entitled": True, "publication_id": publication.id, "status": "paid"}
    prior = (await db.execute(select(ContentOrder).where(ContentOrder.idempotency_key == body.idempotency_key).with_for_update())).scalar_one_or_none()
    if prior:
        if prior.user_id != user.id:
            fail("幂等键已被其他账号使用", 409)
        return {"out_trade_no": prior.out_trade_no, "status": prior.status, "amount_fen": prior.amount_fen}
    out_trade_no = f"MKT{int(time.time())}{user.id:04d}{uuid.uuid4().hex[:10]}"
    order = ContentOrder(out_trade_no=out_trade_no, user_id=user.id, publication_id=publication.id, episode_id=publication.content_id if publication.content_type == "series_episode" else None, amount_fen=int(publication.unlock_price_fen), pay_type=body.pay_type, idempotency_key=body.idempotency_key)
    db.add(order)
    await db.commit()
    try:
        notify_url = f"{get_settings().public_base_url.rstrip('/')}/api/marketplace/payments/epay/notify"
        mapi = await epay.create_mapi_payment(out_trade_no=out_trade_no, name=f"数字资产-{publication.content_id}", money_yuan=epay.money_yuan_from_fen(order.amount_fen), pay_type=body.pay_type, clientip=_client_ip(request), notify_url=notify_url)
        fields = epay.build_submit_fields(out_trade_no=out_trade_no, name=f"数字资产-{publication.content_id}", money_yuan=epay.money_yuan_from_fen(order.amount_fen), pay_type=body.pay_type, clientip=_client_ip(request), notify_url=notify_url)
    except ValueError as exc:
        await db.execute(select(ContentOrder).where(ContentOrder.id == order.id).with_for_update())
        order.status = "failed"
        await db.commit()
        fail(str(exc), 503)
    return {"out_trade_no": order.out_trade_no, "status": order.status, "amount_fen": order.amount_fen, "pay_type": order.pay_type, "trade_no": mapi.get("trade_no") or "", "qrcode": mapi.get("qrcode") or "", "payurl": mapi.get("payurl") or "", "qr_payload": mapi.get("qr_payload") or "", "pay_mode": mapi.get("pay_mode") or "redirect", "submit_url": epay.submit_url(fields)}


@router.get("/content-orders/{out_trade_no}", response_model=dict)
async def get_content_order(out_trade_no: str, db: DB, user: Actor):
    order = (await db.execute(select(ContentOrder).where(ContentOrder.out_trade_no == out_trade_no, ContentOrder.user_id == user.id))).scalar_one_or_none()
    if not order:
        fail("订单不存在", 404)
    return data(order, "out_trade_no", "publication_id", "episode_id", "amount_fen", "pay_type", "status", "trade_no", "paid_at", "created_at")


@router.get("/entitlements", response_model=dict)
async def list_entitlements(db: DB, user: Actor):
    rows = list((await db.execute(select(Entitlement, Publication).join(Publication, Publication.id == Entitlement.publication_id).where(Entitlement.user_id == user.id, Entitlement.active.is_(True)).order_by(Entitlement.id.desc()))).all())
    return {"items": [{**data(entitlement, "id", "publication_id", "episode_id", "created_at"), **data(publication, "content_type", "content_id", "version", "unlock_price_fen")} for entitlement, publication in rows]}


@router.get("/platform/episodes/{episode_id}/playback", response_model=dict, dependencies=[])
async def episode_playback(episode_id: int, db: DB, user: Actor):
    episode = await db.get(SeriesEpisode, episode_id)
    if not episode:
        fail("分集不存在", 404)
    publication = (await db.execute(select(Publication).where(
        Publication.content_type == "series_episode", Publication.content_id == episode.id,
        Publication.version == episode.version, Publication.audit_status == "approved",
        Publication.listed.is_(True), Publication.sale_type == "platform_unlock",
    ))).scalar_one_or_none()
    if not publication or publication.sale_type != "platform_unlock":
        fail("分集尚未公开", 404)
    # Playback always goes through the configured private bucket.  Keep this
    # explicit because the route opts out of the router dependency list so the
    # entitlement check can return a precise 403 first.
    await storage_ready()
    if publication.sale_type == "platform_unlock":
        entitlement = (await db.execute(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.publication_id == publication.id, Entitlement.active.is_(True)))).scalar_one_or_none()
        if not entitlement:
            fail("MARKETPLACE_ENTITLEMENT_REQUIRED", 403)
        if not publication.playback_manifest_key:
            fail("MARKETPLACE_EPISODE_NOT_READY", 409)
        manifest = await _signed_hls_manifest(publication)
        # 返回重写后的清单文本和过期时间；客户端应创建 Blob URL 播放，分片签名与清单同周期。
        return {"manifest": manifest, "kind": "hls", "expires_in": 300}
    asset = await db.get(Asset, publication.asset_id)
    if not asset or asset.status != "ready":
        fail("媒体尚未就绪", 409)
    url = await __import__("asyncio").to_thread(storage.signed, asset.object_key, asset.content_type)
    return {"url": url, "kind": "original", "expires_in": 300}


@router.get("/platform/works/{work_id}/playback", response_model=dict, dependencies=[])
async def work_playback(work_id: int, db: DB, user: Actor):
    work = await db.get(Work, work_id)
    if not work:
        fail("作品不存在", 404)
    publication = (await db.execute(select(Publication).where(
        Publication.content_type == "work", Publication.content_id == work.id,
        Publication.version == work.version, Publication.audit_status == "approved",
        Publication.listed.is_(True), Publication.sale_type == "platform_unlock"))).scalar_one_or_none()
    if not publication or publication.sale_type != "platform_unlock":
        fail("作品尚未公开", 404)
    # Same storage boundary as episode playback; no local/static fallback is
    # allowed for marketplace media.
    await storage_ready()
    if publication.sale_type == "platform_unlock":
        entitlement = (await db.execute(select(Entitlement).where(
            Entitlement.user_id == user.id, Entitlement.publication_id == publication.id,
            Entitlement.active.is_(True)))).scalar_one_or_none()
        if not entitlement:
            fail("MARKETPLACE_ENTITLEMENT_REQUIRED", 403)
        manifest = await _signed_hls_manifest(publication)
        return {"manifest": manifest, "kind": "hls", "expires_in": 300}
    asset = await db.get(Asset, publication.asset_id)
    if not asset or asset.status != "ready":
        fail("媒体尚未就绪", 409)
    url = await __import__("asyncio").to_thread(storage.signed, asset.object_key, asset.content_type)
    return {"url": url, "kind": "original", "expires_in": 300}


def _notify_success(params: dict[str, str]) -> bool:
    return str(params.get("trade_status") or params.get("status") or "").upper() in {"TRADE_SUCCESS", "SUCCESS", "1"}


@router.api_route("/payments/epay/notify", methods=["GET", "POST"])
async def epay_notify(request: Request, db: DB) -> PlainTextResponse:
    if request.method == "POST":
        form = await request.form()
        params = {key: str(value) for key, value in form.items()}
    else:
        params = {key: str(value) for key, value in request.query_params.items()}
    if not epay.verify(params):
        return PlainTextResponse("fail", status_code=400)
    if not _notify_success(params):
        return PlainTextResponse("success")
    trade_no = str(params.get("out_trade_no") or "")
    order = (await db.execute(select(ContentOrder).where(ContentOrder.out_trade_no == trade_no).with_for_update())).scalar_one_or_none()
    if not order:
        return PlainTextResponse("fail", status_code=404)
    try:
        paid_fen = int(round(float(params.get("money") or 0) * 100))
    except ValueError:
        paid_fen = order.amount_fen
    if paid_fen != order.amount_fen:
        return PlainTextResponse("fail", status_code=400)
    if order.status == "paid":
        return PlainTextResponse("success")
    publication = await db.get(Publication, order.publication_id)
    if not publication or not _publication_public(publication):
        return PlainTextResponse("fail", status_code=409)
    # Two checkout attempts can race before the first callback creates the
    # entitlement.  The unique entitlement is the idempotency boundary for a
    # user's permanent access; acknowledge the later payment without creating
    # a second entitlement or revenue ledger row.
    existing_entitlement = (await db.execute(select(Entitlement).where(
        Entitlement.user_id == order.user_id,
        Entitlement.publication_id == publication.id,
        Entitlement.active.is_(True),
    ).with_for_update())).scalar_one_or_none()
    if existing_entitlement:
        order.status, order.trade_no, order.paid_at = "paid", str(params.get("trade_no") or params.get("transaction_id") or "") or None, datetime.now(timezone.utc)
        await db.commit()
        return PlainTextResponse("success")
    order.status, order.trade_no, order.paid_at = "paid", str(params.get("trade_no") or params.get("transaction_id") or "") or None, datetime.now(timezone.utc)
    entitlement = Entitlement(user_id=order.user_id, publication_id=publication.id, episode_id=order.episode_id, order_id=order.id)
    db.add(entitlement)
    config = await db.get(RevenueConfig, 1)
    share = publication.share_bps if publication.share_bps is not None else (config.platform_share_bps if config else None)
    if share is None:
        return PlainTextResponse("fail", status_code=409)
    platform_fen = order.amount_fen * int(share) // 10000
    ledger = RevenueLedger(order_id=order.id, publication_id=publication.id, owner_id=publication.owner_id, gross_fen=order.amount_fen, platform_fen=platform_fen, creator_fen=order.amount_fen - platform_fen, share_bps=int(share))
    db.add(ledger)
    await db.commit()
    return PlainTextResponse("success")

