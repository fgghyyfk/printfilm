"""短剧系列市场 API。

系列数据只使用 marketplace_ 表；原 drama 项目仅在用户明确导入时读取一次快照。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from sqlalchemy import and_, not_, or_, select

from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from .dependencies import Actor, DB, Visitor, storage_ready
from .models import Asset, EpisodePreview, PlaybackJob, Publication, RevenueConfig, Rights, Series, SeriesEpisode
from .schemas import EpisodeInput, EpisodeUpdate, ImportEpisodesInput, SeriesInput, SeriesUpdate, Version
from .service import asset_owned, asset_owned_for_draft, check_version, data, event, fail, now, notify, owned, page, validate_rights

router = APIRouter()


def _sale_type(value: str, copyright_price: int) -> str:
    # 收费类型由用户明确选择；参考价为0仍可以先联系客服询价。
    return value


async def _series(db, series_id: int, user, *, lock: bool = False) -> Series:
    query = select(Series).where(Series.id == series_id)
    if lock:
        query = query.with_for_update()
    item = (await db.execute(query)).scalar_one_or_none()
    if not item or (item.owner_id != user.id and user.role != "admin"):
        fail("系列不存在或无权访问", 404)
    return item


async def _episode(db, episode_id: int, user, *, lock: bool = False) -> SeriesEpisode:
    query = select(SeriesEpisode).where(SeriesEpisode.id == episode_id)
    if lock:
        query = query.with_for_update()
    item = (await db.execute(query)).scalar_one_or_none()
    if not item or (item.owner_id != user.id and user.role != "admin"):
        fail("分集不存在或无权访问", 404)
    return item


async def _episode_data(db, item: SeriesEpisode, *, private: bool = False) -> dict:
    asset = await db.get(Asset, item.asset_id) if item.asset_id else None
    result = data(item, "id", "series_id", "episode_no", "title", "description", "copyright_price_fen", "unlock_price_fen", "share_bps", "source", "source_type", "version", "preview_status", "playback_status", "created_at")
    result.update(media_status=asset.status if asset else "missing", duration_seconds=asset.duration_seconds if asset else None)
    if private:
        result.update(data(item, "owner_id", "asset_id", "cover_asset_id", "audit_status", "listed", "blocked", "reason", "source_snapshot"))
        rights = (await db.execute(select(Rights).where(Rights.asset_id == item.asset_id))).scalar_one_or_none() if item.asset_id else None
        result.update(rights_ready=bool(rights and rights.basic_status == "approved" and rights.full_status == "approved"), rights_source=rights.details.get("source") if rights else None)
    return result


async def _series_data(db, item: Series, *, private: bool = False) -> dict:
    episodes = (await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == item.id).order_by(SeriesEpisode.episode_no))).scalars().all()
    result = data(item, "id", "title", "description", "category", "tags", "cover_asset_id", "sale_type", "copyright_price_fen", "free_episode_count", "created_at")
    result["episode_count"] = len(episodes)
    result["episodes"] = [await _episode_data(db, episode, private=private) for episode in episodes]
    if private:
        result.update(data(item, "owner_id", "version", "audit_status", "listed", "blocked", "reason", "share_bps"))
    return result


def public_series_filters():
    """作品广场只展示已上架、且每一集都有水印文件的短剧。改免费集数不要求重做。"""
    watermarked = or_(
        EpisodePreview.object_key.startswith("marketplace/series-watermarks-full/"),
        EpisodePreview.object_key.startswith("marketplace/series-previews-hq/"),
    )
    missing = select(SeriesEpisode.id).where(
        SeriesEpisode.series_id == Series.id,
        not_(select(EpisodePreview.id).where(
            EpisodePreview.episode_id == SeriesEpisode.id,
            EpisodePreview.asset_id == SeriesEpisode.asset_id,
            EpisodePreview.status == "done",
            watermarked,
        ).exists()),
    ).exists()
    has_episode = select(SeriesEpisode.id).where(SeriesEpisode.series_id == Series.id).exists()
    return (Series.listed.is_(True), Series.blocked.is_(False), Series.deleted_at.is_(None), Series.audit_status == "approved", has_episode, not_(missing))


@router.get("/series", response_model=dict)
async def list_series(db: DB, q: str = Query("", max_length=100), category: str = Query("", max_length=60), page_number: int = Query(1, alias="page", ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(Series).where(*public_series_filters())
    if q:
        query = query.where(Series.title.ilike(f"%{q}%"))
    if category:
        query = query.where(Series.category == category)
    records, meta = await page(db, query.order_by(Series.id.desc()), page_number, page_size)
    return {"items": [await _series_data(db, row) for row in records], "meta": meta}


@router.get("/series/import-sources", response_model=dict)
async def import_sources(db: DB, user: Actor):
    """列出当前用户可明确选择导入的漫剧项目和已生成分集。"""
    from app.services import oss as upstream_oss
    from app.services import storage as upstream_storage
    projects = list((await db.execute(select(DramaProject).where(DramaProject.user_id == user.id).order_by(DramaProject.id.desc()))).scalars().all())
    result = []
    for project in projects:
        episodes = list((await db.execute(select(DramaEpisode).where(DramaEpisode.project_id == project.id).order_by(DramaEpisode.id))).scalars().all())
        episode_items = []
        for episode in episodes:
            fragments = list((await db.execute(select(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == episode.id).order_by(DramaEpisodeFragment.sort_order.desc()))).scalars().all())
            ready = False
            for fragment in fragments:
                raw_video = (fragment.video or "").strip()
                if not raw_video:
                    continue
                local = upstream_storage.local_path_from_url(raw_video)
                if not local:
                    candidate = Path(raw_video)
                    if candidate.is_absolute() and candidate.is_file():
                        try:
                            relative = candidate.resolve().relative_to(upstream_storage.STATIC_ROOT.resolve())
                            local = upstream_storage.STATIC_ROOT / relative
                        except ValueError:
                            local = None
                if (local and local.is_file()) or raw_video.startswith(("http://", "https://")):
                    ready = True
                    break
                # Object-key rows can be checked during import through the
                # existing shared OSS client.  Do not issue a network HEAD for
                # every list request; a missing object returns SOURCE_NOT_READY
                # when the user explicitly imports it.
                if upstream_oss.oss_enabled() and not raw_video.startswith("/static/"):
                    ready = True
                    break
            episode_items.append({"id": episode.id, "name": episode.name, "ready": ready})
        result.append({"id": project.id, "title": project.title, "episodes": episode_items})
    return {"items": result}


@router.get("/series/{series_id}", response_model=dict)
async def get_series(series_id: int, db: DB, user: Visitor):
    item = await db.get(Series, series_id)
    private = bool(item and user and not item.deleted_at and (item.owner_id == user.id or user.role == "admin"))
    if not item or item.deleted_at or (not private and item.blocked):
        fail("系列不存在或未公开", 404)
    if not private:
        visible = (await db.execute(select(Series.id).where(Series.id == series_id, *public_series_filters()))).scalar_one_or_none()
        if not visible:
            fail("系列不存在或未公开", 404)
    return await _series_data(db, item, private=private)


@router.post("/series", response_model=dict)
async def create_series(body: SeriesInput, db: DB, user: Actor):
    from .categories import validate_category
    await validate_category(db, body.category)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    values = body.model_dump()
    if "sale_type" not in body.model_fields_set and values["copyright_price_fen"] == 0:
        values["sale_type"] = "free"
    item = Series(owner_id=user.id, **values)
    item.sale_type = _sale_type(item.sale_type, item.copyright_price_fen)
    db.add(item)
    await db.commit()
    return await _series_data(db, item, private=True)


@router.patch("/series/{series_id}", response_model=dict)
async def update_series(series_id: int, body: SeriesUpdate, db: DB, user: Actor):
    item = await _series(db, series_id, user, lock=True)
    from .service import ensure_kept
    ensure_kept(item)
    check_version(item, body.version)
    from .categories import validate_category
    await validate_category(db, body.category, item.category)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    values = body.model_dump(exclude={"version"})
    if "sale_type" not in body.model_fields_set and values["copyright_price_fen"] == 0:
        values["sale_type"] = "free"
    values["sale_type"] = _sale_type(values["sale_type"], values["copyright_price_fen"])
    for key, value in values.items():
        setattr(item, key, value)
    item.version += 1
    item.audit_status, item.listed, item.reason = "draft", False, ""
    await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == item.id).with_for_update())
    await db.commit()
    return await _series_data(db, item, private=True)


@router.post("/series/{series_id}/episodes", response_model=dict)
async def create_episode(series_id: int, body: EpisodeInput, db: DB, user: Actor):
    series = await _series(db, series_id, user, lock=True)
    duplicate = (await db.execute(select(SeriesEpisode.id).where(SeriesEpisode.series_id == series.id, SeriesEpisode.episode_no == body.episode_no))).scalar_one_or_none()
    if duplicate:
        fail("该集数已经存在", 409)
    if body.asset_id:
        await asset_owned_for_draft(db, body.asset_id, user)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    item = SeriesEpisode(series_id=series.id, owner_id=user.id, source_type="upload", **body.model_dump())
    if series.sale_type == "platform_unlock" and item.unlock_price_fen <= 0:
        fail("平台投放系列必须为每集设置解锁价")
    db.add(item)
    series.version += 1
    series.audit_status, series.listed = "draft", False
    await db.commit()
    return await _episode_data(db, item, private=True)


@router.patch("/series/{series_id}/episodes/{episode_id}", response_model=dict)
async def update_episode(series_id: int, episode_id: int, body: EpisodeUpdate, db: DB, user: Actor):
    series = await _series(db, series_id, user, lock=True)
    item = await _episode(db, episode_id, user, lock=True)
    if item.series_id != series.id:
        fail("分集不属于该系列", 404)
    check_version(item, body.version)
    if body.asset_id:
        await asset_owned_for_draft(db, body.asset_id, user)
    if body.cover_asset_id:
        await asset_owned(db, body.cover_asset_id, user, ("image",))
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(item, key, value)
    item.version += 1
    item.audit_status, item.listed, item.reason = "draft", False, ""
    series.version += 1
    series.audit_status, series.listed = "draft", False
    await db.commit()
    return await _episode_data(db, item, private=True)


@router.delete("/series/{series_id}/episodes/{episode_id}", response_model=dict)
async def delete_episode(series_id: int, episode_id: int, db: DB, user: Actor):
    series = await _series(db, series_id, user, lock=True)
    item = await _episode(db, episode_id, user, lock=True)
    if item.series_id != series.id:
        fail("分集不属于该系列", 404)
    await db.delete(item)
    series.version += 1
    series.audit_status, series.listed = "draft", False
    await db.commit()
    return {"ok": True}


@router.post("/series/{series_id}/episodes/import", response_model=dict)
async def import_episodes(series_id: int, body: ImportEpisodesInput, db: DB, user: Actor):
    """只导入用户明确选择的漫剧分集。复制交给市场 Worker，请求本身不等待长视频。"""
    from .handoff import enqueue_series_import
    from .schemas import PrintfilmEpisodeInput

    payload = PrintfilmEpisodeInput(drama_project_id=body.drama_project_id, episode_ids=list(body.episode_ids))
    return await enqueue_series_import(series_id, payload, db, user)


@router.delete("/series/{series_id}", response_model=dict)
async def delete_series(series_id: int, db: DB, user: Actor):
    """作者软删整部短剧。分集记录保留，但不再出现在广场和我的作品。"""
    item = await _series(db, series_id, user, lock=True)
    from .service import retire_owned
    await retire_owned(db, item, user, "series")
    await db.commit()
    return {"ok": True}


@router.post("/series/{series_id}/submit", response_model=dict)
async def submit_series(series_id: int, body: Version, db: DB, user: Actor):
    series = await _series(db, series_id, user, lock=True)
    from .service import ensure_kept
    ensure_kept(series)
    check_version(series, body.version)
    if series.blocked:
        fail("系列已被管理员封禁")
    episodes = list((await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == series.id).order_by(SeriesEpisode.episode_no).with_for_update())).scalars().all())
    if not episodes:
        fail("至少添加一集后才能提交审核")
    if [episode.episode_no for episode in episodes] != list(range(1, len(episodes) + 1)):
        fail("分集编号必须从1开始连续排列")
    config = await db.get(RevenueConfig, 1)
    if series.sale_type == "platform_unlock" and (not config or not config.enabled):
        fail("MARKETPLACE_REVENUE_CONFIG_REQUIRED", 409)
    # Paid series require a real private bucket because the submission creates
    # a preview job that will immediately be consumed by the market Worker.
    # Reject early with the standard 503 instead of leaving a misleading pending
    # job when OSS is not configured.
    await storage_ready()
    from .previews import is_watermark_key
    pending_preview = False
    for episode in episodes:
        asset = await asset_owned(db, episode.asset_id or 0, user)
        if not episode.cover_asset_id and not series.cover_asset_id:
            fail(f"第{episode.episode_no}集缺少封面，请上传分集封面或系列封面", 409)
        rights = (await db.execute(select(Rights).where(Rights.asset_id == asset.id).with_for_update())).scalar_one_or_none()
        if not rights or rights.blocked:
            fail("请完成每集版权信息后再提交", 409)
        if rights.details.get("source") != episode.source:
            fail(f"第{episode.episode_no}集版权来源与分集来源不匹配", 409)
        validate_rights(rights.details, full=True, certificate=bool(rights.certificate_asset_id))
        if series.sale_type == "platform_unlock" and episode.unlock_price_fen <= 0:
            fail(f"第{episode.episode_no}集必须设置解锁价")
        # 同一原片已有水印就沿用，改免费集数或收费类型不重新转码。
        done = (await db.execute(select(EpisodePreview).where(
            EpisodePreview.episode_id == episode.id, EpisodePreview.asset_id == asset.id,
            EpisodePreview.status == "done").order_by(EpisodePreview.id.desc()).limit(1))).scalar_one_or_none()
        preview = (await db.execute(select(EpisodePreview).where(
            EpisodePreview.episode_id == episode.id, EpisodePreview.asset_id == asset.id,
            EpisodePreview.version == episode.version).with_for_update())).scalar_one_or_none()
        if done and is_watermark_key(done.object_key):
            if preview is None:
                preview = EpisodePreview(episode_id=episode.id, asset_id=asset.id, version=episode.version, status="done", object_key=done.object_key)
                db.add(preview)
            elif preview.id != done.id:
                preview.status, preview.object_key, preview.error = "done", done.object_key, ""
            preview_status = "done"
        else:
            if preview is None:
                preview = EpisodePreview(episode_id=episode.id, asset_id=asset.id, version=episode.version)
                db.add(preview)
            elif preview.status == "failed":
                preview.status, preview.attempts, preview.error, preview.object_key = "pending", 0, "", None
                preview.lease_token, preview.lease_until = None, None
            preview_status = "done" if preview.status == "done" and is_watermark_key(preview.object_key) else "pending"
        pending_preview = pending_preview or preview_status != "done"
        publication = (await db.execute(select(Publication).where(
            Publication.content_type == "series_episode", Publication.content_id == episode.id,
            Publication.version == episode.version).with_for_update())).scalar_one_or_none()
        values = dict(owner_id=user.id, version=episode.version, asset_id=asset.id,
                      cover_asset_id=episode.cover_asset_id or series.cover_asset_id, sale_type=series.sale_type,
                      copyright_price_fen=episode.copyright_price_fen or series.copyright_price_fen,
                      unlock_price_fen=episode.unlock_price_fen, share_bps=episode.share_bps,
                      rights_snapshot={"details": rights.details, "certificate_asset_id": rights.certificate_asset_id,
                                       "evidence_ids": rights.evidence_ids}, audit_status="pending", listed=False,
                      preview_status=preview_status, playback_status="done" if series.sale_type != "platform_unlock" else "pending")
        # Keep the episode's live access gate in sync with its immutable
        # publication.  In particular, a paid episode must be marked pending
        # immediately so /media cannot fall back to the original while the
        # watermark job is still running.
        episode.preview_status = preview_status
        episode.playback_status = "done" if series.sale_type != "platform_unlock" else "pending"
        if publication is None:
            publication = Publication(content_type="series_episode", content_id=episode.id, **values)
            db.add(publication)
        else:
            for key, value in values.items():
                setattr(publication, key, value)
        if series.sale_type == "platform_unlock":
            await db.flush()
            playback_job = (await db.execute(select(PlaybackJob).where(
                PlaybackJob.publication_id == publication.id
            ).with_for_update())).scalar_one_or_none()
            if playback_job is None:
                db.add(PlaybackJob(publication_id=publication.id, status="pending"))
            else:
                playback_job.status, playback_job.error = "pending", ""
                playback_job.attempts = 0
                playback_job.lease_token, playback_job.lease_until = None, None
            publication.playback_manifest_key = None
            publication.playback_error = ""
        episode.audit_status = "pending"
        episode.listed = False
        rights.basic_status = rights.full_status = "pending"
    series.audit_status = "processing" if pending_preview else "pending"
    series.listed, series.reason = False, "正在生成水印播放文件" if pending_preview else ""
    event(db, user.id, "series", series.id, "submitted", version=series.version)
    await db.commit()
    return await _series_data(db, series, private=True)


@router.post("/series/{series_id}/listing", response_model=dict)
async def listing_series(series_id: int, body: Version, db: DB, user: Actor, listed: bool = True):
    series = await _series(db, series_id, user, lock=True)
    from .service import ensure_kept
    ensure_kept(series)
    check_version(series, body.version)
    # Lock and snapshot the current episode versions once.  Historical
    # publications are kept for entitlements but must never be relisted when a
    # newer episode version is approved.
    episodes = list((await db.execute(
        select(SeriesEpisode).where(SeriesEpisode.series_id == series.id).with_for_update()
    )).scalars().all())
    if listed:
        if series.blocked or series.audit_status != "approved":
            fail("系列内容或权属尚未通过，不能上架")
        if not episodes:
            fail("系列没有可上架的分集")
        from .previews import is_watermark_key
        for episode in episodes:
            if episode.blocked or episode.audit_status != "approved":
                fail(f"第{episode.episode_no}集尚未审核通过")
            preview = (await db.execute(select(EpisodePreview).where(
                EpisodePreview.episode_id == episode.id, EpisodePreview.asset_id == episode.asset_id,
                EpisodePreview.status == "done").order_by(EpisodePreview.id.desc()).limit(1))).scalar_one_or_none()
            if not preview or not is_watermark_key(preview.object_key):
                fail(f"第{episode.episode_no}集水印播放文件尚未生成", 409)
            if series.sale_type == "platform_unlock":
                publication = (await db.execute(select(Publication).where(Publication.content_type == "series_episode", Publication.content_id == episode.id, Publication.version == episode.version))).scalar_one_or_none()
                if not publication or publication.playback_status != "done":
                    fail(f"第{episode.episode_no}集播放版本尚未就绪", 409)
    series.listed = listed
    episode_ids = [episode.id for episode in episodes]
    publications = []
    if episode_ids:
        publications = list((await db.execute(
            select(Publication).where(
                Publication.content_type == "series_episode",
                Publication.content_id.in_(episode_ids),
            ).with_for_update()
        )).scalars().all())
    for publication in publications:
        is_current = any(
            episode.id == publication.content_id and episode.version == publication.version
            for episode in episodes
        )
        publication.listed = listed and is_current and publication.audit_status == "approved" and (
            series.sale_type != "platform_unlock" or publication.playback_status == "done"
        )
    for episode in episodes:
        episode.listed = listed and episode.audit_status == "approved" and not episode.blocked
    await db.commit()
    return await _series_data(db, series, private=True)

