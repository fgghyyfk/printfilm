"""把 PrintFilm 成片异步复制到市场前缀。

接口只接受项目或分集编号。视频地址由服务端读取并记在转存记录里，
复制、指纹和探测由现有市场 Worker 完成，不在请求里下载整段视频。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.database import AsyncSessionLocal
from app.models import Project
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from .authorization import current as current_authorization
from .categories import category_config
from .config import get_marketplace_settings
from .dependencies import Actor, DB, storage_ready
from .models import Asset, Handoff, SeriesEpisode, Work
from .schemas import PrintfilmEpisodeInput, PrintfilmWorkInput
from .service import data, fail, notify, now

logger = logging.getLogger("marketplace.handoff")
router = APIRouter()
VIDEO_LIMIT = 2 * 1024 * 1024 * 1024
IMAGE_LIMIT = 20 * 1024 * 1024
_VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def object_key(locator: str, *, bucket: str, public_base: str, folder: str) -> str | None:
    """从本站 OSS 地址或对象键取出同桶复制用的 key；本地地址不在这里解析。"""
    text = (locator or "").strip()
    if not text or text.startswith("/static/") or text.startswith("marketplace/"):
        return None
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        path = unquote(parsed.path).lstrip("/")
        host = parsed.netloc.split("@")[-1]
        if bucket and host.startswith(f"{bucket}."):
            return path or None
        if bucket and path.startswith(f"{bucket}/"):
            path = path[len(bucket) + 1 :]
            return path or None
        base = public_base.rstrip("/")
        bare = text.split("?", 1)[0]
        if bare.startswith(base + "/"):
            return unquote(bare[len(base) + 1 :]) or None
        marker = f"/{folder.strip('/')}/"
        index = parsed.path.find(marker)
        if index >= 0:
            return unquote(parsed.path[index + 1 :].split("?", 1)[0]) or None
        return None
    key = text.split("?", 1)[0].lstrip("/")
    if not key or key.startswith("marketplace/") or key.startswith("static/"):
        return None
    prefix = folder.strip("/")
    if prefix and not key.startswith(prefix + "/") and "/" not in key:
        key = f"{prefix}/{key}"
    return key


def _version(marker: str, locator: str) -> str:
    digest = hashlib.sha256(locator.encode()).hexdigest()[:16]
    return f"{marker}:{digest}"[:80]


def _item_key(prefix: str, source_kind: str, source_id: int, version: str) -> str:
    raw = f"{prefix}:{source_kind}:{source_id}" if prefix else f"{source_kind}:{source_id}:{version}"
    return raw if len(raw) <= 120 else hashlib.sha256(raw.encode()).hexdigest()


def _suffix(locator: str, default: str) -> str:
    suffix = Path(locator.split("?", 1)[0]).suffix.lower()
    if suffix in _VIDEO_TYPES or suffix in _IMAGE_TYPES:
        return suffix
    return default


def _content_type(suffix: str, default: str) -> str:
    return _VIDEO_TYPES.get(suffix) or _IMAGE_TYPES.get(suffix) or default


def _local_file(locator: str) -> Path | None:
    """只接受 static 目录内的成片，避免按任意本地路径读取。"""
    from app.services import storage as upstream

    local = upstream.local_path_from_url(locator)
    if local and local.is_file():
        return local
    candidate = Path(locator)
    if not candidate.is_absolute() or not candidate.is_file():
        return None
    try:
        relative = candidate.resolve().relative_to(upstream.STATIC_ROOT.resolve())
    except ValueError:
        return None
    resolved = upstream.STATIC_ROOT / relative
    return resolved if resolved.is_file() else None


def place_object(locator: str, dest_key: str, content_type: str, limit: int) -> int:
    """同桶复制，或由服务器读取本地成片后上传。不向浏览器返回源地址。"""
    from app.config import get_settings
    from app.services import oss as upstream_oss
    from . import storage

    local = _local_file(locator)
    if local:
        size = local.stat().st_size
        if size <= 0 or size > limit:
            raise ValueError("成片大小无效或超过限制")
        storage.upload(dest_key, local, content_type)
        return size
    settings = get_settings()
    key = object_key(locator, bucket=settings.oss_bucket, public_base=upstream_oss.public_base(), folder=upstream_oss.folder_prefix())
    if not key:
        raise ValueError("成片尚未就绪")
    size = storage.copy_private(key, dest_key, content_type)
    if size <= 0 or size > limit:
        raise ValueError("成片大小无效或超过限制")
    return size


async def _view(db, item: Handoff) -> dict:
    """给页面的状态。不返回源地址、对象键或指纹原文以外的内部快照。"""
    authorization = await current_authorization(db, item.owner_id)
    ready = bool(authorization)
    if item.status == "failed":
        label = "复制失败可重试"
    elif item.status == "ready":
        label = "已就绪" if ready else "待补充授权"
    else:
        label = "正在同步"
    payload = data(item, "id", "status", "target_kind", "target_id", "series_id", "asset_id", "attempts", "created_at")
    payload.update(label=label, authorization_ready=ready, error=item.error if item.status == "failed" else "",
                   work_id=item.target_id if item.target_kind == "work" else None,
                   episode_id=item.target_id if item.target_kind == "series_episode" else None)
    return payload


async def _owned_handoff(db, handoff_id: int, user) -> Handoff:
    item = await db.get(Handoff, handoff_id)
    if not item or item.owner_id != user.id:
        fail("转存记录不存在", 404)
    return item


async def _find(db, owner_id: int, key: str) -> Handoff | None:
    return (await db.execute(select(Handoff).where(Handoff.owner_id == owner_id, Handoff.idempotency_key == key))).scalar_one_or_none()


async def _category(db, preferred: str) -> str:
    names = (await category_config(db))["names"]
    if preferred in names:
        return preferred
    if "其他" in names:
        return "其他"
    return names[0] if names else "其他"


def _clip(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


async def _project_source(db, user_id: int, project_id: int) -> tuple[Project, str]:
    project = await db.get(Project, project_id)
    if not project or project.user_id != user_id:
        fail("成片不存在或无权访问", 404)
    locator = (project.final_video_url or "").strip()
    if not locator:
        fail("成片尚未生成", 409)
    return project, locator


async def _drama_source(db, user_id: int, drama_project_id: int, episode_id: int) -> tuple[DramaProject, DramaEpisode, DramaEpisodeFragment, str]:
    project = (await db.execute(select(DramaProject).where(DramaProject.id == drama_project_id, DramaProject.user_id == user_id))).scalar_one_or_none()
    if not project:
        fail("漫剧项目不存在或无权访问", 404)
    episode = (await db.execute(select(DramaEpisode).where(DramaEpisode.id == episode_id, DramaEpisode.project_id == project.id))).scalar_one_or_none()
    if not episode:
        fail("存在不属于该漫剧项目的分集", 404)
    fragments = list((await db.execute(select(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == episode.id).order_by(DramaEpisodeFragment.sort_order.desc(), DramaEpisodeFragment.id.desc()))).scalars().all())
    fragment = next((item for item in fragments if (item.video or "").strip()), None)
    if not fragment:
        fail("所选分集没有可导入的成片，请先完成分集合成", 409)
    return project, episode, fragment, fragment.video.strip()


async def _next_no(db, series_id: int) -> int:
    current = (await db.execute(select(func.max(SeriesEpisode.episode_no)).where(SeriesEpisode.series_id == series_id))).scalar_one()
    return int(current or 0) + 1


def _handoff(owner_id: int, key: str, source_kind: str, source_id: int, version: str, target_kind: str, target_id: int, series_id: int | None, locator: str, cover: str, title: str) -> Handoff:
    return Handoff(owner_id=owner_id, idempotency_key=key, source_kind=source_kind, source_id=source_id, source_version=version,
                   target_kind=target_kind, target_id=target_id, series_id=series_id, status="pending",
                   source_snapshot={"locator": locator, "cover_locator": cover, "title": title})


@router.get("/printfilm/sources", response_model=dict)
async def printfilm_sources(db: DB, user: Actor):
    """只列出当前用户已经生成成片的科普项目和漫剧分集，不返回视频地址。"""
    from .series import import_sources

    projects = list((await db.execute(select(Project).where(Project.user_id == user.id, Project.final_video_url.is_not(None), Project.final_video_url != "").order_by(Project.updated_at.desc()).limit(50))).scalars().all())
    dramas = await import_sources(db, user)
    return {"projects": [{"id": item.id, "title": item.title, "updated_at": item.updated_at, "has_cover": bool(item.cover_url), "status": item.status} for item in projects], "dramas": dramas["items"]}


@router.post("/works/from-printfilm", response_model=dict)
async def work_from_printfilm(body: PrintfilmWorkInput, db: DB, user: Actor):
    """把一部科普成片登记为市场草稿。同一成片版本重复点击返回原来的草稿。"""
    await storage_ready()
    project, locator = await _project_source(db, user.id, body.project_id)
    version = _version(project.updated_at.isoformat(), locator)
    key = _item_key(body.idempotency_key.strip(), "project_final", project.id, version)
    existing = await _find(db, user.id, key)
    if existing:
        return await _view(db, existing)
    work = Work(owner_id=user.id, title=_clip(project.title or "未命名作品", 200) or "未命名作品", description=_clip(project.source_text or "", 5000),
                category=await _category(db, "科普"), price_fen=0, sale_type="copyright_contact", source="printfilm", audit_status="draft")
    db.add(work)
    await db.flush()
    db.add(_handoff(user.id, key, "project_final", project.id, version, "work", work.id, None, locator, (project.cover_url or "").strip(), work.title))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _find(db, user.id, key)
        if existing:
            return await _view(db, existing)
        raise
    created = await _find(db, user.id, key)
    return await _view(db, created)


async def enqueue_series_import(series_id: int, body: PrintfilmEpisodeInput, db, user) -> dict:
    """把选中的漫剧分集或一部科普成片登记为短剧分集，实际复制交给 Worker。"""
    await storage_ready()
    from .series import _episode_data, _series

    series = await _series(db, series_id, user, lock=True)
    from .service import ensure_kept
    ensure_kept(series)
    if series.owner_id != user.id:
        fail("只能导入到自己的短剧", 403)
    jobs: list[tuple[str, str, int, str, str, str, str]] = []
    if body.project_id:
        project, locator = await _project_source(db, user.id, body.project_id)
        version = _version(project.updated_at.isoformat(), locator)
        jobs.append((_item_key(body.idempotency_key.strip(), f"series{series.id}:project_final", project.id, version), "project_final", project.id, version, locator, (project.cover_url or "").strip(), _clip(project.title or "第1集", 200) or "第1集"))
    else:
        for episode_id in dict.fromkeys(body.episode_ids):
            _project, episode, fragment, locator = await _drama_source(db, user.id, body.drama_project_id or 0, episode_id)
            version = _version(f"{fragment.id}:{fragment.updated_at.isoformat()}", locator)
            jobs.append((_item_key(body.idempotency_key.strip(), f"series{series.id}:drama_episode", episode.id, version), "drama_episode", episode.id, version, locator, (fragment.cover or "").strip(), _clip(episode.name or "", 200) or f"分集{episode.id}"))
    created: list[SeriesEpisode] = []
    records: list[Handoff] = []
    added = False
    for key, source_kind, source_id, version, locator, cover, title in jobs:
        existing = await _find(db, user.id, key)
        if existing:
            if existing.target_kind == "series_episode" and existing.series_id == series.id:
                episode = await db.get(SeriesEpisode, existing.target_id)
                if episode:
                    created.append(episode)
            records.append(existing)
            continue
        episode_no = await _next_no(db, series.id)
        item = SeriesEpisode(series_id=series.id, owner_id=user.id, episode_no=episode_no, title=title or f"第{episode_no}集", source="printfilm", source_type="drama_import",
                             source_snapshot={"source_kind": source_kind, "source_id": source_id, "source_title": title})
        db.add(item)
        await db.flush()
        handoff = _handoff(user.id, key, source_kind, source_id, version, "series_episode", item.id, series.id, locator, cover, item.title)
        db.add(handoff)
        created.append(item)
        records.append(handoff)
        added = True
    if added:
        series.version += 1
        series.audit_status, series.listed = "draft", False
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        records = []
        created = []
        for key, *_rest in jobs:
            existing = await _find(db, user.id, key)
            if not existing:
                continue
            records.append(existing)
            episode = await db.get(SeriesEpisode, existing.target_id) if existing.target_kind == "series_episode" else None
            if episode and episode.series_id == series.id:
                created.append(episode)
    return {"items": [await _episode_data(db, item, private=True) for item in created], "handoffs": [await _view(db, item) for item in records]}


@router.post("/series/{series_id}/episodes/import-printfilm", response_model=dict)
async def import_printfilm(series_id: int, body: PrintfilmEpisodeInput, db: DB, user: Actor):
    """明确选中的漫剧分集，或一部科普成片，转成短剧分集草稿。"""
    return await enqueue_series_import(series_id, body, db, user)


@router.get("/handoffs/{handoff_id}", response_model=dict)
async def get_handoff(handoff_id: int, db: DB, user: Actor):
    """查看自己的转存进度。"""
    return await _view(db, await _owned_handoff(db, handoff_id, user))


@router.get("/handoffs", response_model=dict)
async def list_handoffs(db: DB, user: Actor, target_kind: str = "", target_id: int = 0, series_id: int = 0):
    """按作品、分集或系列查看当前用户的转存进度。"""
    query = select(Handoff).where(Handoff.owner_id == user.id)
    if target_kind and target_id:
        query = query.where(Handoff.target_kind == target_kind, Handoff.target_id == target_id)
    if series_id:
        query = query.where(Handoff.series_id == series_id)
    rows = list((await db.execute(query.order_by(Handoff.id.desc()).limit(50))).scalars().all())
    return {"items": [await _view(db, item) for item in rows]}


@router.post("/handoffs/{handoff_id}/retry", response_model=dict)
async def retry_handoff(handoff_id: int, db: DB, user: Actor):
    """失败后重新排队。已复制完成的记录保持不变。"""
    await storage_ready()
    item = await _owned_handoff(db, handoff_id, user)
    if item.status != "failed":
        return await _view(db, item)
    item.status, item.error, item.attempts = "pending", "", 0
    item.lease_token, item.lease_until = None, None
    await db.commit()
    return await _view(db, item)


async def _claim() -> tuple[int, str] | None:
    async with AsyncSessionLocal() as db:
        query = select(Handoff).where(or_(Handoff.status == "pending", and_(Handoff.status == "running", Handoff.lease_until < now()))).order_by(Handoff.id).with_for_update(skip_locked=True).limit(1)
        item = (await db.execute(query)).scalar_one_or_none()
        if not item:
            return None
        if item.attempts >= get_marketplace_settings().worker_max_attempts:
            item.status = "failed"
            item.error = item.error or "复制重试次数已用完，请稍后重试"
            link = f"/marketplace/works/{item.target_id}/edit?handoff={item.id}" if item.target_kind == "work" else f"/marketplace/series/{item.series_id}/edit"
            notify(db, item.owner_id, "成片复制失败，可在发布页重试", link)
            await db.commit()
            return None
        item.attempts += 1
        item.status, item.lease_token, item.lease_until = "running", uuid.uuid4().hex, now() + timedelta(minutes=15)
        item.error = ""
        await db.commit()
        return item.id, item.lease_token


async def _heartbeat(handoff_id: int, token: str) -> None:
    while True:
        await asyncio.sleep(20)
        async with AsyncSessionLocal() as db:
            result = await db.execute(update(Handoff).where(Handoff.id == handoff_id, Handoff.lease_token == token, Handoff.status == "running").values(lease_until=now() + timedelta(minutes=15)))
            await db.commit()
            if not result.rowcount:
                raise RuntimeError("转存任务租约已丢失")


async def _fail(handoff_id: int, token: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        item = (await db.execute(select(Handoff).where(Handoff.id == handoff_id, Handoff.lease_token == token).with_for_update())).scalar_one_or_none()
        if not item:
            return
        terminal = item.attempts >= get_marketplace_settings().worker_max_attempts
        item.status = "failed" if terminal else "pending"
        item.error = message[:500]
        item.lease_token, item.lease_until = None, None
        if item.asset_id:
            asset = await db.get(Asset, item.asset_id)
            if asset and asset.status == "processing":
                asset.status, asset.error = "failed", message[:500]
        if terminal:
            link = f"/marketplace/works/{item.target_id}/edit?handoff={item.id}" if item.target_kind == "work" else f"/marketplace/series/{item.series_id}/edit"
            notify(db, item.owner_id, "成片复制失败，可在发布页重试", link)
        await db.commit()


async def _store_cover(locator: str, owner_id: int, folder: str) -> tuple[str, int, str] | None:
    if not locator:
        return None
    suffix = _suffix(locator, "")
    if suffix not in _IMAGE_TYPES:
        return None
    key = f"{folder}/cover-{uuid.uuid4().hex}{suffix}"
    try:
        size = await asyncio.to_thread(place_object, locator, key, _content_type(suffix, "image/png"), IMAGE_LIMIT)
    except Exception:
        logger.info("市场转存封面跳过")
        return None
    return key, size, _content_type(suffix, "image/png")


async def _copy(handoff_id: int, token: str) -> None:
    """复制到 marketplace/ 后探测时长和指纹，再把资产标为 ready。"""
    async with AsyncSessionLocal() as db:
        item = (await db.execute(select(Handoff).where(Handoff.id == handoff_id, Handoff.lease_token == token))).scalar_one_or_none()
        if not item:
            return
        snapshot = dict(item.source_snapshot or {})
        owner_id, target_kind, target_id = item.owner_id, item.target_kind, item.target_id
        if item.asset_id:
            asset = await db.get(Asset, item.asset_id)
            if asset and asset.status == "ready":
                await _bind(db, item, asset.id, None)
                item.status, item.error = "ready", ""
                item.lease_token, item.lease_until = None, None
                await db.commit()
                return
            if asset and asset.status == "processing":
                asset.status, asset.error = "failed", "已重新复制"
                await db.commit()
    locator = str(snapshot.get("locator") or "")
    suffix = _suffix(locator, ".mp4")
    content_type = _content_type(suffix, "video/mp4")
    folder = f"marketplace/{'works' if target_kind == 'work' else 'series'}/u{owner_id}/{target_id}"
    dest = f"{folder}/{uuid.uuid4().hex}{suffix}"
    try:
        size = await asyncio.to_thread(place_object, locator, dest, content_type, VIDEO_LIMIT)
    except HTTPException:
        await _fail(handoff_id, token, "文件服务暂未配置或不可用")
        return
    except ValueError as exc:
        await _fail(handoff_id, token, str(exc))
        return
    except Exception:
        logger.exception("marketplace handoff %s copy failed", handoff_id)
        await _fail(handoff_id, token, "复制成片失败，请稍后重试")
        return
    filename = f"{_clip(str(snapshot.get('title') or 'video'), 80) or 'video'}{suffix}"
    async with AsyncSessionLocal() as db:
        item = (await db.execute(select(Handoff).where(Handoff.id == handoff_id, Handoff.lease_token == token).with_for_update())).scalar_one_or_none()
        if not item:
            return
        asset = Asset(owner_id=owner_id, kind="video", filename=filename[:255], content_type=content_type, size_bytes=size, object_key=dest, status="processing")
        db.add(asset)
        await db.flush()
        item.asset_id = asset.id
        asset_id = asset.id
        probe = {"id": asset.id, "filename": asset.filename, "object_key": dest, "kind": "video", "size_bytes": size, "content_type": content_type}
        await db.commit()
    from .worker import process_asset
    try:
        result = await process_asset(probe)
    except ValueError as exc:
        await _fail(handoff_id, token, str(exc))
        return
    except Exception:
        logger.exception("marketplace handoff %s probe failed", handoff_id)
        await _fail(handoff_id, token, "成片无法解析，请确认文件完整后重试")
        return
    cover = await _store_cover(str(snapshot.get("cover_locator") or ""), owner_id, folder)
    async with AsyncSessionLocal() as db:
        item = (await db.execute(select(Handoff).where(Handoff.id == handoff_id, Handoff.lease_token == token).with_for_update())).scalar_one_or_none()
        asset = await db.get(Asset, asset_id) if item else None
        if not item or not asset:
            return
        asset.fingerprint = str(result.get("fingerprint") or "")
        asset.duration_seconds = result.get("duration_seconds")
        asset.status, asset.error = "ready", ""
        item.source_fingerprint = asset.fingerprint
        cover_id = None
        if cover:
            cover_asset = Asset(owner_id=owner_id, kind="image", filename=f"cover{Path(cover[0]).suffix}", content_type=cover[2], size_bytes=cover[1], object_key=cover[0], status="ready")
            db.add(cover_asset)
            await db.flush()
            cover_id = cover_asset.id
        await _bind(db, item, asset.id, cover_id)
        item.status, item.error = "ready", ""
        item.lease_token, item.lease_until = None, None
        await db.commit()


async def _bind(db, item: Handoff, asset_id: int, cover_id: int | None) -> None:
    """把就绪资产挂到草稿上；不改审核状态，也不生成水印。"""
    if item.target_kind == "work":
        work = await db.get(Work, item.target_id)
        if work and work.owner_id == item.owner_id:
            work.asset_id = asset_id
            if cover_id and not work.cover_asset_id:
                work.cover_asset_id = cover_id
        return
    episode = await db.get(SeriesEpisode, item.target_id)
    if episode and episode.owner_id == item.owner_id:
        episode.asset_id = asset_id
        if cover_id and not episode.cover_asset_id:
            episode.cover_asset_id = cover_id


async def run_handoff_once() -> bool:
    """领取一条转存。没有待处理记录时返回 False。"""
    claimed = await _claim()
    if not claimed:
        return False
    handoff_id, token = claimed
    pulse = asyncio.create_task(_heartbeat(handoff_id, token))
    try:
        await _copy(handoff_id, token)
    except Exception:
        logger.exception("marketplace handoff %s failed", handoff_id)
        await _fail(handoff_id, token, "复制成片失败，请稍后重试")
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
    return True
