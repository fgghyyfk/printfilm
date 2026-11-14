"""统一公开播放策略：免费原片，收费使用经校验的独立短试看。"""
from fastapi import HTTPException
from sqlalchemy import or_, select

from .models import Asset, EpisodePreview, Publication, RevenueConfig, Rights, Series, SeriesEpisode, Work, WorkPreview

CLIP_WORK_PREFIX = "marketplace/previews-v2/works/"
CLIP_EPISODE_PREFIX = "marketplace/previews-v2/episodes/"
PREVIEW_LEASE_PREFIX = "preview-v2:"


def is_preview_key(key: str | None, *, episode: bool | None = None) -> bool:
    """旧完整水印和旧短水印均不符合当前策略，不能公开签发。"""
    prefixes = (CLIP_EPISODE_PREFIX,) if episode is True else (CLIP_WORK_PREFIX,) if episode is False else (CLIP_WORK_PREFIX, CLIP_EPISODE_PREFIX)
    return bool(key and key.startswith(prefixes))


def is_watermark_key(key: str | None) -> bool:
    """兼容旧调用名称；只认可新版无水印短试看。"""
    return is_preview_key(key)


def episode_is_free(series, episode) -> bool:
    return getattr(series, "sale_type", None) == "free" or 0 < int(getattr(episode, "episode_no", 0) or 0) <= int(getattr(series, "free_episode_count", 0) or 0)


def requires_preview(sale_type: str) -> bool:
    return sale_type != "free"


def preview_duration(duration: float) -> float:
    if not 0 < duration < float("inf"):
        raise ValueError("视频时长无效")
    return min(15.0, duration * 0.3)


async def preview_for(db, work, *, lock=False):
    query = select(WorkPreview).where(WorkPreview.work_id == work.id, WorkPreview.asset_id == work.asset_id)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


async def preview_ready(db, work):
    if not requires_preview(work.sale_type):
        return True
    item = await preview_for(db, work)
    return bool(item and item.version == work.version and item.status == "done" and is_preview_key(item.object_key, episode=False))


def _reset(job):
    job.status, job.attempts, job.error, job.object_key = "pending", 0, "", None
    job.lease_token, job.lease_until = None, None


async def cancel_work_previews(db, work):
    jobs = list((await db.execute(select(WorkPreview).where(WorkPreview.work_id == work.id,
        WorkPreview.status.in_(("pending", "running"))).with_for_update())).scalars().all())
    for job in jobs:
        job.status, job.lease_token, job.lease_until = "cancelled", None, None


async def cancel_episode_previews(db, episode):
    jobs = list((await db.execute(select(EpisodePreview).where(EpisodePreview.episode_id == episode.id,
        EpisodePreview.status.in_(("pending", "running"))).with_for_update())).scalars().all())
    for job in jobs:
        job.status, job.lease_token, job.lease_until = "cancelled", None, None


async def prepare_preview(db, work):
    """同一资产的新版合格短片可复用；旧版本在途任务必须撤销租约。"""
    item = await preview_for(db, work, lock=True)
    if item is None:
        item = WorkPreview(work_id=work.id, asset_id=work.asset_id, version=work.version, status="pending")
        db.add(item)
    else:
        reusable = item.status == "done" and is_preview_key(item.object_key, episode=False)
        if not reusable and (item.version != work.version or item.status not in ("pending", "running") or item.object_key):
            _reset(item)
        item.version = work.version
    return item


async def episode_preview_for(db, episode, *, lock=False):
    query = select(EpisodePreview).where(EpisodePreview.episode_id == episode.id,
        EpisodePreview.asset_id == episode.asset_id, EpisodePreview.version == episode.version)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


async def episode_preview_ready(db, series, episode):
    if episode_is_free(series, episode):
        return True
    item = await episode_preview_for(db, episode)
    return bool(item and item.status == "done" and is_preview_key(item.object_key, episode=True))


async def prepare_episode_preview(db, episode):
    item = await episode_preview_for(db, episode, lock=True)
    if item and item.status == "done" and is_preview_key(item.object_key, episode=True):
        return item
    reusable = (await db.execute(select(EpisodePreview).where(EpisodePreview.episode_id == episode.id,
        EpisodePreview.asset_id == episode.asset_id, EpisodePreview.status == "done",
        EpisodePreview.object_key.startswith(CLIP_EPISODE_PREFIX)).order_by(EpisodePreview.id.desc()).limit(1))).scalar_one_or_none()
    if item is None:
        item = EpisodePreview(episode_id=episode.id, asset_id=episode.asset_id, version=episode.version, status="pending")
        db.add(item)
    elif item.status not in ("pending", "running") or item.object_key:
        _reset(item)
    if reusable:
        item.status, item.object_key, item.error = "done", reusable.object_key, ""
        item.lease_token, item.lease_until = None, None
    return item


async def _materials_ready(db, owner_id, asset_id, source, cover_id=None, *, cover_required=False):
    from .service import now, validate_rights
    asset = await db.get(Asset, asset_id) if asset_id else None
    if not asset or asset.owner_id != owner_id or asset.kind != "video" or asset.status != "ready":
        return False
    if cover_required and not cover_id:
        return False
    if cover_id:
        cover = await db.get(Asset, cover_id)
        if not cover or cover.owner_id != owner_id or cover.kind != "image" or cover.status != "ready":
            return False
    rights = (await db.execute(select(Rights).where(Rights.asset_id == asset_id))).scalar_one_or_none()
    if not rights or rights.owner_id != owner_id or rights.blocked or rights.details.get("source") != source:
        return False
    if rights.valid_until and rights.valid_until <= now():
        return False
    try:
        validate_rights(rights.details, full=True, certificate=bool(rights.certificate_asset_id))
    except HTTPException:
        return False
    for document_id in [*rights.evidence_ids, *([rights.certificate_asset_id] if rights.certificate_asset_id else [])]:
        document = await db.get(Asset, document_id)
        if not document or document.owner_id != owner_id or document.status != "ready":
            return False
    if source == "printfilm":
        from .authorization import current
        authorization = await current(db, owner_id)
        if not authorization or str(authorization.id) != str(rights.details.get("authorization_id")):
            return False
    return True


async def work_submission_ready(db, work):
    if work.deleted_at or work.blocked or not work.license_scope.strip():
        return False
    if work.sale_type == "platform_unlock":
        config = await db.get(RevenueConfig, 1)
        if not config or not config.enabled or work.unlock_price_fen <= 0:
            return False
    return await _materials_ready(db, work.owner_id, work.asset_id, work.source, work.cover_asset_id)


async def series_submission_ready(db, series):
    if series.deleted_at or series.blocked:
        return False
    episodes = list((await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == series.id)
        .order_by(SeriesEpisode.episode_no))).scalars().all())
    if not episodes or [e.episode_no for e in episodes] != list(range(1, len(episodes) + 1)):
        return False
    if series.sale_type == "platform_unlock":
        config = await db.get(RevenueConfig, 1)
        if not config or not config.enabled:
            return False
    for episode in episodes:
        if episode.blocked or episode.owner_id != series.owner_id:
            return False
        if series.sale_type == "platform_unlock" and not episode_is_free(series, episode) and episode.unlock_price_fen <= 0:
            return False
        if not await _materials_ready(db, series.owner_id, episode.asset_id, episode.source,
            episode.cover_asset_id or series.cover_asset_id, cover_required=True):
            return False
    return True


async def reconcile_preview_policy(db):
    """启动前迁移当前任务策略；保留旧文件、历史发布版本和作者上下架选择。"""
    works = list((await db.execute(select(Work).order_by(Work.id).with_for_update())).scalars().all())
    for work in works:
        stale = list((await db.execute(select(WorkPreview).where(WorkPreview.work_id == work.id,
            WorkPreview.status.in_(("pending", "running")),
            or_(WorkPreview.asset_id != work.asset_id, WorkPreview.version != work.version)).with_for_update())).scalars().all())
        for job in stale:
            job.status, job.lease_token, job.lease_until = "cancelled", None, None
        if work.deleted_at or work.blocked or not requires_preview(work.sale_type):
            await cancel_work_previews(db, work)
        if work.deleted_at or work.blocked:
            continue
        if not requires_preview(work.sale_type):
            if work.audit_status in ("processing", "preview_failed"):
                ready = await work_submission_ready(db, work)
                work.audit_status, work.reason = ("pending", "") if ready else ("draft", "资料已变化，请补齐后重新提交审核")
                publications = list((await db.execute(select(Publication).where(Publication.content_type == "work",
                    Publication.content_id == work.id, Publication.version == work.version,
                    Publication.audit_status != "approved").with_for_update())).scalars().all())
                for publication in publications:
                    publication.preview_status, publication.preview_key, publication.preview_error = "none", None, ""
                    publication.audit_status = "pending" if ready else "draft"
            continue
        if not work.asset_id or work.audit_status not in ("processing", "preview_failed", "pending", "approved"):
            continue
        existing = await preview_for(db, work, lock=True)
        if existing and existing.status == "done" and existing.version == work.version and is_preview_key(existing.object_key, episode=False):
            continue
        if existing and existing.version == work.version and existing.status == "failed" and (existing.lease_token or "").startswith(PREVIEW_LEASE_PREFIX):
            if work.audit_status != "approved":
                work.audit_status, work.reason = "preview_failed", existing.error
            continue
        if existing and existing.status == "running" and not (existing.lease_token or "").startswith(PREVIEW_LEASE_PREFIX):
            _reset(existing)
        await prepare_preview(db, work)
        if work.audit_status != "approved":
            work.audit_status, work.reason = "processing", "正在生成短试看"

    series_rows = list((await db.execute(select(Series).order_by(Series.id).with_for_update())).scalars().all())
    for series in series_rows:
        episodes = list((await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == series.id)
            .order_by(SeriesEpisode.id).with_for_update())).scalars().all())
        active = series.audit_status in ("processing", "preview_failed", "pending", "approved")
        pending, failed = False, False
        for episode in episodes:
            stale = list((await db.execute(select(EpisodePreview).where(EpisodePreview.episode_id == episode.id,
                EpisodePreview.status.in_(("pending", "running")),
                or_(EpisodePreview.asset_id != episode.asset_id, EpisodePreview.version != episode.version)).with_for_update())).scalars().all())
            for job in stale:
                job.status, job.lease_token, job.lease_until = "cancelled", None, None
            if series.deleted_at or series.blocked or episode.blocked or episode_is_free(series, episode):
                await cancel_episode_previews(db, episode)
                if episode_is_free(series, episode):
                    episode.preview_status, episode.preview_key, episode.preview_attempts = "none", None, 0
                continue
            if not active or not episode.asset_id:
                continue
            existing = await episode_preview_for(db, episode, lock=True)
            if existing and existing.status == "done" and is_preview_key(existing.object_key, episode=True):
                continue
            if existing and existing.status == "failed" and (existing.lease_token or "").startswith(PREVIEW_LEASE_PREFIX):
                failed = True
                episode.preview_status = "failed"
                continue
            if existing and existing.status == "running" and not (existing.lease_token or "").startswith(PREVIEW_LEASE_PREFIX):
                _reset(existing)
            preview = await prepare_episode_preview(db, episode)
            episode.preview_status, episode.preview_key = preview.status or "pending", preview.object_key
            pending = pending or preview.status != "done"
        if active and not series.deleted_at and not series.blocked and series.audit_status != "approved":
            if failed:
                series.audit_status, series.reason = "preview_failed", "短试看生成失败，可重试"
            elif pending:
                series.audit_status, series.reason = "processing", "正在生成短试看"
            elif await series_submission_ready(db, series):
                series.audit_status, series.reason = "pending", ""
                for episode in episodes:
                    publications = list((await db.execute(select(Publication).where(Publication.content_type == "series_episode",
                        Publication.content_id == episode.id, Publication.version == episode.version,
                        Publication.audit_status != "approved").with_for_update())).scalars().all())
                    for publication in publications:
                        if episode_is_free(series, episode):
                            publication.preview_status, publication.preview_key, publication.preview_error = "none", None, ""
                        publication.audit_status = "pending"
            else:
                series.audit_status, series.reason = "draft", "资料已变化，请补齐后重新提交审核"


async def withdraw_unwatermarked_public(db):
    """兼容旧启动入口，实际执行新版试看策略协调。"""
    await reconcile_preview_policy(db)
