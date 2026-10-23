"""作品试看任务由现有独立 Worker 串行执行，不接入上游 AI 调度。"""
import asyncio
from datetime import timedelta
import json
import logging
import math
from pathlib import Path
import tempfile
import uuid

from sqlalchemy import and_, or_, select

from app.config import get_settings
from app.database import AsyncSessionLocal
from . import storage
from .config import get_marketplace_settings
from .models import Asset, EpisodePreview, Publication, Series, SeriesEpisode, Work, WorkPreview
from .previews import episode_is_free, is_watermark_key, plays_full_watermark, preview_duration
from .service import event, now, notify

logger = logging.getLogger(__name__)


def watermark_text(author_name: str | None) -> str:
    """水印为平台名、作者显示名和侵权声明。去掉会破坏转码参数的字符。"""
    raw = (author_name or "").strip() or "创作者"
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in "\\':%{}[]")
    cleaned = " ".join(cleaned.split())[:20] or "创作者"
    return f"PRINTFILM  {cleaned}  侵权必究"


async def render_preview(snapshot):
    from .worker import cache_root, command
    settings, upstream = get_marketplace_settings(), get_settings()
    with tempfile.TemporaryDirectory(prefix="job-", dir=cache_root()) as directory:
        root = Path(directory)
        source = root / ("source" + Path(snapshot["filename"]).suffix.lower())
        await asyncio.to_thread(storage.download, snapshot["source_key"], source)
        if source.stat().st_size != snapshot["size_bytes"]:
            raise ValueError("原片大小不一致")
        # mp4 是首版上传主格式；保留其他已支持容器，避免 ffprobe 因白名单误拒绝正常文件。
        formats = "mov,matroska,webm,mp4"
        probe = json.loads(await command(upstream.ffprobe_path, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", formats, "-show_format", "-show_streams", "-of", "json", str(source), timeout=60))
        videos = [v for v in probe.get("streams", []) if v.get("codec_type") == "video"]
        if not videos or not 0 < int(videos[0].get("width", 0)) * int(videos[0].get("height", 0)) <= 50_000_000:
            raise ValueError("无效的视频尺寸")
        duration = float(probe.get("format", {}).get("duration", 0))
        full = bool(snapshot.get("full_watermark"))
        if duration <= 0 or duration > settings.max_duration_seconds:
            raise ValueError("视频超过时长限制" if duration > settings.max_duration_seconds else "视频时长无效")
        limit_args: list[str] = []
        timeout = 600
        if full:
            timeout = min(7200, max(600, int(duration * 4) + 120))
        else:
            limit = preview_duration(duration)
            # 为视频帧和 AAC 尾帧预留余量，不能因封装取整超出 30% / 15 秒。
            limit = math.floor((limit - 0.1) * 30) / 30
            if limit <= 0:
                raise ValueError("视频过短，无法生成受限试看")
            limit_args = ["-t", str(limit)]
        font = Path(settings.watermark_font)
        if not font.is_file():
            raise ValueError("水印字体不可用")
        escaped_font = str(font.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        # 作者名已去掉转码特殊字符；上下两处烧录。
        content_id = snapshot.get("content_id", snapshot.get("work_id"))
        watermark = watermark_text(snapshot.get("author_name"))
        # 不缩放画面；奇数尺寸只补至偶数，兼容 H.264 的 yuv420p。
        filters = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
        for x, y in (("(w-tw)/2", "h*0.22"), ("(w-tw)/2", "h*0.72")):
            filters += (f",drawtext=fontfile='{escaped_font}':text='{watermark}':fontsize=h/30:"
                        f"fontcolor=white@0.7:box=1:boxcolor=black@0.25:x={x}:y={y}")
        output = root / "preview.mp4"
        await command(upstream.ffmpeg_path, "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", formats, "-i", str(source), *limit_args, "-map", "0:v:0", "-map", "0:a:0?",
            "-sn", "-dn", "-vf", filters, "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-threads", "2", "-fps_mode", "passthrough", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(output), timeout=timeout)
        # 每次尝试独立对象，过期进程不能覆盖已签发的文件。
        # Older callers/tests only supplied work_id; treat those snapshots as
        # single-video works for backwards compatibility.  New series jobs pass
        # content_type explicitly so their preview is isolated under its own
        # prefix.
        content = snapshot.get("content_type", "work")
        if full:
            folder = "series-watermarks-full" if content == "series_episode" else "work-watermarks-full"
        else:
            folder = "work-previews-hq" if content == "work" else "series-previews-hq"
        prefix = folder
        key = f"marketplace/{prefix}/{content_id}/{snapshot['asset_id']}/{uuid.uuid4().hex}.mp4"
        await asyncio.to_thread(storage.upload, key, output, "video/mp4")
        return key


async def run_preview_once():
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(WorkPreview).where(or_(WorkPreview.status == "pending",
            and_(WorkPreview.status == "running", WorkPreview.lease_until < now())))
            .order_by(WorkPreview.id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if not job:
            return False
        asset = await db.get(Asset, job.asset_id)
        work = await db.get(Work, job.work_id)
        token = uuid.uuid4().hex
        job.status, job.lease_token, job.lease_until = "running", token, now() + timedelta(minutes=2)
        job.attempts += 1
        job_id, work_id = job.id, job.work_id
        from app.models import User
        owner = await db.get(User, work.owner_id)
        snapshot = dict(content_type="work", content_id=work_id, work_id=work_id, owner_id=work.owner_id,
            author_name=owner.nickname if owner and owner.nickname else "创作者", asset_id=asset.id,
            filename=asset.filename, source_key=asset.object_key, size_bytes=asset.size_bytes,
            attempts=job.attempts, full_watermark=plays_full_watermark(work.sale_type, work.price_fen))
        await db.commit()

    async def heartbeat():
        from sqlalchemy import update
        while True:
            await asyncio.sleep(20)
            async with AsyncSessionLocal() as db:
                changed = await db.execute(update(WorkPreview).where(WorkPreview.id == job_id,
                    WorkPreview.lease_token == token, WorkPreview.status == "running")
                    .values(lease_until=now() + timedelta(minutes=2)))
                await db.commit()
                if not changed.rowcount:
                    raise RuntimeError("试看任务租约失效")

    key, error = None, ""
    pulse = asyncio.create_task(heartbeat())
    async def bounded_render():
        if snapshot["attempts"] > get_marketplace_settings().worker_max_attempts:
            raise ValueError("试看任务重试次数已用完")
        return await render_preview(snapshot)
    processing = asyncio.create_task(bounded_render())
    try:
        done, _ = await asyncio.wait((pulse, processing), return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse
            raise RuntimeError("试看任务心跳中断")
        key = await processing
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("作品试看生成失败 %s", job_id)
        error = "试看生成失败，请检查原片、字体及 OSS 配置后重试"
    finally:
        pulse.cancel()
        processing.cancel()
        await asyncio.gather(pulse, processing, return_exceptions=True)
    async with AsyncSessionLocal() as db:
        # 与提交接口统一为先锁作品、后锁任务。
        work = await db.get(Work, work_id, with_for_update=True)
        job = (await db.execute(select(WorkPreview).where(WorkPreview.id == job_id,
            WorkPreview.lease_token == token).with_for_update())).scalar_one_or_none()
        if not job:
            return True
        matching = (work.version == job.version and work.asset_id == job.asset_id
                    and work.audit_status == "processing" and not work.blocked)
        if key:
            job.object_key, job.status, job.error = key, "done", ""
            publications = (await db.execute(select(Publication).where(Publication.content_type == "work", Publication.content_id == work_id, Publication.version == job.version).with_for_update())).scalars().all()
            for publication in publications:
                publication.preview_key, publication.preview_status, publication.preview_error = key, "done", ""
            if matching:
                work.audit_status, work.reason = "pending", ""
                event(db, work.owner_id, "work", work.id, "preview_ready", version=work.version)
                notify(db, work.owner_id, f"作品《{work.title}》水印播放文件已生成，已进入待审核",
                       f"/marketplace/works/{work.id}/manage")
            elif work and work.audit_status == "approved" and not work.blocked and not work.listed and key.startswith(expected):
                work.listed = True
                notify(db, work.owner_id, f"作品《{work.title}》完整水印已生成，已重新出现在作品广场",
                       f"/marketplace/works/{work.id}")
        else:
            terminal = job.attempts >= get_marketplace_settings().worker_max_attempts
            job.status, job.error = "failed" if terminal else "pending", error
            if matching and terminal:
                work.audit_status, work.reason = "preview_failed", error
            publications = (await db.execute(select(Publication).where(Publication.content_type == "work", Publication.content_id == work_id, Publication.version == job.version).with_for_update())).scalars().all()
            for publication in publications:
                publication.preview_status, publication.preview_error = ("failed" if terminal else "pending"), error
                notify(db, work.owner_id, f"作品《{work.title}》试看生成失败，可在发布管理中重试",
                       f"/marketplace/works/{work.id}/manage")
        job.lease_token, job.lease_until = None, None
        await db.commit()
    return True


async def _advance_series_review(db, series_id: int) -> None:
    """全部分集水印完成后，短剧才进入待审核。"""
    series = await db.get(Series, series_id, with_for_update=True)
    if not series or series.audit_status != "processing":
        return
    episodes = list((await db.execute(select(SeriesEpisode).where(SeriesEpisode.series_id == series.id))).scalars().all())
    failed = False
    ready = bool(episodes)
    for item in episodes:
        preview = (await db.execute(select(EpisodePreview).where(
            EpisodePreview.episode_id == item.id, EpisodePreview.asset_id == item.asset_id,
        ).order_by(EpisodePreview.id.desc()).limit(1))).scalar_one_or_none()
        if preview and preview.status == "failed":
            failed = True
        elif not preview or preview.status != "done" or not is_watermark_key(preview.object_key):
            ready = False
    if failed:
        series.audit_status, series.reason = "preview_failed", "水印播放文件生成失败，请在发布页重试"
        notify(db, series.owner_id, f"短剧《{series.title}》水印生成失败，可在发布页重试", f"/marketplace/series/{series.id}/edit")
    elif ready:
        series.audit_status, series.reason = "pending", ""
        notify(db, series.owner_id, f"短剧《{series.title}》水印已生成，已进入待审核", f"/marketplace/series/{series.id}/edit")


async def release_finished_series_once() -> bool:
    """水印已经完成、但仍停在生成中的短剧推进到待审核。"""
    async with AsyncSessionLocal() as db:
        series_ids = list((await db.execute(select(Series.id).where(Series.audit_status == "processing"))).scalars().all())
        if not series_ids:
            return False
        for series_id in series_ids:
            await _advance_series_review(db, series_id)
        await db.commit()
        return True


async def run_episode_preview_once():
    """为非免费剧集生成限时烧录水印试看，并同步不可变 Publication。"""
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(EpisodePreview).where(
            or_(EpisodePreview.status == "pending",
                and_(EpisodePreview.status == "running", EpisodePreview.lease_until < now())))
            .order_by(EpisodePreview.id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if not job:
            return False
        asset = await db.get(Asset, job.asset_id)
        episode = await db.get(SeriesEpisode, job.episode_id)
        if not asset or not episode:
            job.status, job.error = "failed", "分集或媒体不存在"
            await db.commit()
            return True
        token = uuid.uuid4().hex
        job.status, job.lease_token, job.lease_until = "running", token, now() + timedelta(minutes=2)
        job.attempts += 1
        episode.preview_status, episode.preview_attempts = "running", job.attempts
        job_id, episode_id, version = job.id, episode.id, job.version
        series = await db.get(Series, episode.series_id)
        from app.models import User
        owner = await db.get(User, episode.owner_id)
        snapshot = dict(content_type="series_episode", content_id=episode_id, owner_id=episode.owner_id,
                        author_name=owner.nickname if owner and owner.nickname else "创作者",
                        asset_id=asset.id, filename=asset.filename, source_key=asset.object_key,
                        size_bytes=asset.size_bytes, attempts=job.attempts,
                        full_watermark=bool(series and episode_is_free(series, episode)))
        await db.commit()

    async def heartbeat():
        from sqlalchemy import update
        while True:
            await asyncio.sleep(20)
            async with AsyncSessionLocal() as db:
                changed = await db.execute(update(EpisodePreview).where(
                    EpisodePreview.id == job_id, EpisodePreview.lease_token == token,
                    EpisodePreview.status == "running").values(lease_until=now() + timedelta(minutes=2)))
                await db.commit()
                if not changed.rowcount:
                    raise RuntimeError("剧集试看任务租约失效")

    key, error = None, ""
    pulse = asyncio.create_task(heartbeat())
    processing = asyncio.create_task(render_preview(snapshot))
    try:
        done, _ = await asyncio.wait((pulse, processing), return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse
            raise RuntimeError("剧集试看任务心跳中断")
        key = await processing
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("剧集试看生成失败 %s", job_id)
        error = "试看生成失败，请检查原片、字体及 OSS 配置后重试"
    finally:
        pulse.cancel()
        processing.cancel()
        await asyncio.gather(pulse, processing, return_exceptions=True)

    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(EpisodePreview).where(
            EpisodePreview.id == job_id, EpisodePreview.lease_token == token).with_for_update())).scalar_one_or_none()
        if not job:
            return True
        episode = await db.get(SeriesEpisode, episode_id)
        series = await db.get(Series, episode.series_id, with_for_update=True) if episode else None
        episode = await db.get(SeriesEpisode, episode_id, with_for_update=True) if episode else None
        publications = list((await db.execute(select(Publication).where(
            Publication.content_type == "series_episode", Publication.content_id == episode_id,
            Publication.version == version).with_for_update())).scalars().all())
        if key:
            job.object_key, job.status, job.error = key, "done", ""
            if episode:
                episode.preview_key, episode.preview_status = key, "done"
            for publication in publications:
                publication.preview_key, publication.preview_status, publication.preview_error = key, "done", ""
        else:
            terminal = job.attempts >= get_marketplace_settings().worker_max_attempts
            job.status, job.error = ("failed" if terminal else "pending"), error
            if episode:
                episode.preview_status = "failed" if terminal else "pending"
            for publication in publications:
                publication.preview_status, publication.preview_error = ("failed" if terminal else "pending"), error
        if episode:
            await _advance_series_review(db, episode.series_id)
        job.lease_token, job.lease_until = None, None
        await db.commit()
    return True
