"""公开播放文件策略；任何失败都不退回无水印原片。"""
from sqlalchemy import and_, or_, select

from .models import EpisodePreview, Series, SeriesEpisode, Work, WorkPreview

FULL_WORK_PREFIX = "marketplace/work-watermarks-full/"
CLIP_WORK_PREFIX = "marketplace/work-previews-hq/"
FULL_EPISODE_PREFIX = "marketplace/series-watermarks-full/"
CLIP_EPISODE_PREFIX = "marketplace/series-previews-hq/"


def is_watermark_key(key: str | None) -> bool:
    """已生成的水印文件，不论是完整时长还是短试看。"""
    return bool(key and (key.startswith(FULL_WORK_PREFIX) or key.startswith(CLIP_WORK_PREFIX) or key.startswith(FULL_EPISODE_PREFIX) or key.startswith(CLIP_EPISODE_PREFIX)))


def episode_is_free(series, episode) -> bool:
    """集数不超过创作者设置的免费集数时，该集免费观看。"""
    return int(getattr(episode, "episode_no", 0) or 0) <= int(getattr(series, "free_episode_count", 0) or 0)


def plays_full_watermark(sale_type: str, price_fen: int, *, free_watch: bool = False) -> bool:
    """只有首次为免费观看生成水印时才做完整时长；已有文件不因改类型重做。"""
    return bool(free_watch) or sale_type == "free" or (sale_type == "copyright_contact" and int(price_fen or 0) == 0)


def requires_preview(sale_type: str) -> bool:
    """公开播放都要有水印文件，免费内容也不返回原片。"""
    return True


def playback_prefix(sale_type: str, price_fen: int, *, episode: bool = False, free_watch: bool = False) -> str:
    """新水印任务使用的对象前缀。已完成的文件不按这个前缀作废。"""
    if plays_full_watermark(sale_type, price_fen, free_watch=free_watch):
        return FULL_EPISODE_PREFIX if episode else FULL_WORK_PREFIX
    return CLIP_EPISODE_PREFIX if episode else CLIP_WORK_PREFIX


def preview_duration(duration: float) -> float:
    """付费试看时长：不超过 15 秒，且不超过原片 30%。"""
    if not 0 < duration < float("inf"):
        raise ValueError("视频时长无效")
    return min(15.0, duration * 0.3)


async def preview_for(db, work, *, lock=False):
    """读取与当前原片绑定的水印任务。"""
    query = select(WorkPreview).where(WorkPreview.work_id == work.id, WorkPreview.asset_id == work.asset_id)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


async def preview_ready(db, work):
    """同一原片已有水印即可，改收费类型不要求重做。"""
    item = await preview_for(db, work)
    return bool(item and item.status == "done" and is_watermark_key(item.object_key))


async def prepare_preview(db, work):
    """同一原片只保留一条水印任务；失败才重试，已完成的不因改类型作废。"""
    item = await preview_for(db, work, lock=True)
    if item is None:
        item = WorkPreview(work_id=work.id, asset_id=work.asset_id, version=work.version)
        db.add(item)
    else:
        item.version = work.version
        if item.status == "failed":
            item.status, item.attempts, item.error, item.object_key = "pending", 0, "", None
            item.lease_token, item.lease_until = None, None
    return item


async def withdraw_unwatermarked_public(db) -> None:
    """启动时收紧历史收费内容，免费内容始终直接播放原片。

    旧版本可能把收费内容错误上架但没有试看文件；这些记录需要暂时下架
    并重新排队。免费内容不创建任务、不下架。
    """
    from .service import notify
    works = (await db.execute(select(Work).where(
        Work.listed.is_(True),
        Work.sale_type != "free",
    ))).scalars().all()
    for work in works:
        if not work.asset_id or await preview_ready(db, work):
            continue
        work.listed = False
        await prepare_preview(db, work)
        notify(db, work.owner_id, f"作品《{work.title}》已暂时下架。试看文件生成后，可在审核通过后重新上架。",
               f"/marketplace/works/{work.id}/manage")
    series_rows = (await db.execute(select(Series).where(
        Series.listed.is_(True),
        Series.sale_type != "free",
    ))).scalars().all()
    for series in series_rows:
        episodes = (await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == series.id))).scalars().all()
        missing = False
        for episode in episodes:
            if not episode.asset_id:
                missing = True
                continue
            item = (await db.execute(select(EpisodePreview).where(
                EpisodePreview.episode_id == episode.id, EpisodePreview.asset_id == episode.asset_id,
                EpisodePreview.version == episode.version,
            ))).scalar_one_or_none()
            # 免费集是完整水印，付费集是短试看。两种都算已生成，不能因前缀不同把成品清掉。
            ready = bool(item and item.status == "done" and is_watermark_key(item.object_key))
            if ready:
                continue
            missing = True
            episode.listed = False
            if item is None:
                db.add(EpisodePreview(episode_id=episode.id, asset_id=episode.asset_id, version=episode.version))
            elif item.status not in ("running", "pending") and not is_watermark_key(item.object_key):
                item.status, item.attempts, item.error = "pending", 0, ""
                item.lease_token, item.lease_until = None, None
        if missing:
            series.listed = False
            notify(db, series.owner_id, f"短剧《{series.title}》已暂时下架。各集试看文件生成后，可重新上架到作品广场。",
                   f"/marketplace/series/{series.id}/edit")
