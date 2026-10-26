"""受限分片转发到私有 OSS；本地不配置时明确拒绝。"""
import asyncio
from datetime import timedelta
import math
from pathlib import PurePath
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from . import storage
from .config import get_marketplace_settings
from .dependencies import Actor, DB, storage_ready
from .models import Asset, MediaJob, Upload
from .schemas import UploadInput
from .service import data, event, fail, now, owned

router = APIRouter()
# 只允许不能执行脚本的业务文件类型；由 Worker 检查真实文件头。
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@router.post("/uploads", response_model=dict, dependencies=[Depends(storage_ready)])
async def initiate_upload(body: UploadInput, db: DB, user: Actor):
    """上传会话使用共用 Bucket 的市场目录和随机对象名。"""
    settings = get_marketplace_settings()
    filename = body.filename.replace("\\", "/").split("/")[-1]
    extension = PurePath(filename).suffix.lower()
    allowed = VIDEO_TYPES if body.kind == "video" else {**IMAGE_TYPES, **({".pdf": "application/pdf"} if body.kind == "document" else {})}
    if extension not in allowed:
        fail("文件格式不支持")
    if body.kind == "qr" and user.role != "admin":
        fail("只有管理员可上传客服二维码", 403)
    limit = settings.max_video_bytes if body.kind == "video" else settings.max_document_bytes
    if body.size_bytes > limit:
        fail("文件超过允许大小", 413)
    # 同一用户的开放会话上限限制未完成分片资源占用。
    from sqlalchemy import func
    active = (await db.execute(select(func.count()).select_from(Upload).where(Upload.owner_id == user.id, Upload.state == "uploading", Upload.expires_at > now()))).scalar_one()
    if active >= 10:
        fail("未完成上传过多，请先完成或取消", 429)
    key = f"marketplace/originals/u{user.id}/{uuid.uuid4().hex}{extension}"
    remote_id = await asyncio.to_thread(storage.initiate, key, allowed[extension])
    asset = Asset(owner_id=user.id, kind=body.kind, filename=filename, content_type=allowed[extension], size_bytes=body.size_bytes, object_key=key)
    db.add(asset)
    await db.flush()
    upload = Upload(asset_id=asset.id, owner_id=user.id, remote_id=remote_id, part_size=settings.part_size, expires_at=now() + timedelta(hours=24))
    db.add(upload)
    try:
        await db.commit()
    except Exception:
        await asyncio.to_thread(storage.abort, key, remote_id)
        raise
    return {**data(upload, "id", "asset_id", "state", "part_size", "expires_at"), "uploaded_parts": []}


@router.get("/uploads/{upload_id}", response_model=dict)
async def upload_status(upload_id: int, db: DB, user: Actor):
    """客户端恢复相同文件时查询已完成分片，不返回 OSS 凭证。"""
    item = await owned(db, Upload, upload_id, user)
    return {**data(item, "id", "asset_id", "state", "part_size", "expires_at"), "uploaded_parts": sorted(int(n) for n in item.parts)}


@router.put("/uploads/{upload_id}/parts/{number}", response_model=dict, dependencies=[Depends(storage_ready)])
async def put_part(upload_id: int, number: int, request: Request, db: DB, user: Actor):
    """流式读取且最多缓存一个分片；持锁避免 JSON 分片记录丢失。"""
    item = await owned(db, Upload, upload_id, user)
    asset = await db.get(Asset, item.asset_id)
    if item.state != "uploading" or item.expires_at <= now():
        fail("上传已结束或过期", 409)
    count = math.ceil(asset.size_bytes / item.part_size)
    if number < 1 or number > count:
        fail("分片编号无效")
    expected = min(item.part_size, asset.size_bytes - (number - 1) * item.part_size)
    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > expected:
            fail("分片超过预期大小", 413)
        buffer.extend(chunk)
    if len(buffer) != expected:
        fail("分片字节数不匹配")
    import hashlib
    digest = hashlib.sha256(buffer).hexdigest()
    previous = item.parts.get(str(number))
    if previous and previous.get("sha256") == digest:
        return {"ok": True, "number": number}
    etag = await asyncio.to_thread(storage.part, asset.object_key, item.remote_id, number, bytes(buffer))
    item.parts = {**item.parts, str(number): {"etag": etag, "sha256": digest}}
    await db.commit()
    return {"ok": True, "number": number}


@router.post("/uploads/{upload_id}/complete", response_model=dict, dependencies=[Depends(storage_ready)])
async def finish_upload(upload_id: int, db: DB, user: Actor):
    """OSS 完成和真实大小校验后创建唯一持久化媒体任务。"""
    item = await owned(db, Upload, upload_id, user)
    if item.state == "completed":
        return {"asset_id": item.asset_id, "state": item.state}
    asset = await db.get(Asset, item.asset_id)
    if item.state != "uploading" or item.expires_at <= now():
        fail("上传已结束或过期", 409)
    count = math.ceil(asset.size_bytes / item.part_size)
    if set(item.parts) != {str(i) for i in range(1, count + 1)}:
        fail("分片不完整")
    await asyncio.to_thread(storage.complete, asset.object_key, item.remote_id, item.parts)
    if await asyncio.to_thread(storage.head, asset.object_key) != asset.size_bytes:
        fail("OSS 文件大小校验失败")
    # 上传本身完成即就绪；只有招募投稿会在提交时创建媒体审片任务。
    item.state, asset.status = "completed", "ready"
    event(db, user.id, "asset", asset.id, "upload_completed")
    await db.commit()
    return {"asset_id": asset.id, "state": "completed"}


@router.delete("/uploads/{upload_id}", response_model=dict, dependencies=[Depends(storage_ready)])
async def cancel_upload(upload_id: int, db: DB, user: Actor):
    """取消只中止未完成 multipart，不删除已提交的证据或媒体。"""
    item = await owned(db, Upload, upload_id, user)
    if item.state == "completed":
        fail("上传已完成，不能取消")
    asset = await db.get(Asset, item.asset_id)
    await asyncio.to_thread(storage.abort, asset.object_key, item.remote_id)
    item.state, asset.status = "cancelled", "cancelled"
    await db.commit()
    return {"ok": True}


@router.get("/assets/{asset_id}", response_model=dict)
async def asset_status(asset_id: int, db: DB, user: Actor):
    """文件检测未成功前不允许用于业务，失败不伪装上传完成。"""
    asset = await owned(db, Asset, asset_id, user, admin=True)
    return data(asset, "id", "kind", "filename", "status", "duration_seconds", "fingerprint", "error", "created_at")


@router.post("/assets/{asset_id}/retry", response_model=dict, dependencies=[Depends(storage_ready)])
async def retry_asset(asset_id: int, db: DB, user: Actor):
    """显式重试失败任务，不重复创建媒体记录。"""
    asset = await owned(db, Asset, asset_id, user, admin=True)
    job = (await db.execute(select(MediaJob).where(MediaJob.asset_id == asset_id).with_for_update())).scalar_one_or_none()
    if not job or job.status != "failed":
        fail("该文件没有可重试的失败任务")
    job.status, job.attempts, job.error = "pending", 0, ""
    asset.status, asset.error = "processing", ""
    await db.commit()
    return {"ok": True}
