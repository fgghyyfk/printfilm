"""Async OSS upload queue: enqueue after local save, then backfill DB URLs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from copy import deepcopy
from uuid import uuid4
from typing import Any

import redis
from sqlalchemy import String, cast, or_, select, update

from app.config import get_settings
from app.services import oss as oss_svc
from app.services import storage

logger = logging.getLogger(__name__)

_ENQUEUE_KEY = "ai_movie:oss:enqueued:{digest}"
_ENQUEUE_TTL = 7200
_upload_tasks: dict[str, asyncio.Task] = {}
_backfill_task: asyncio.Task | None = None
_next_backfill_at = 0.0
# 稳定主键+行内URL游标；缺文件/失败URL不会永远挡住后面的素材。
_scan_source = 0
_scan_row = None
_scan_after_url: str | None = None

# JSON params 内可能存本地媒体 URL 的字段
_ASSET_PARAM_URL_KEYS = ("voiceAudio", "cover", "url", "image", "video")


def _redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _digest(local_url: str) -> str:
    return hashlib.sha1(local_url.encode("utf-8")).hexdigest()[:24]


def _claim_upload(local_url: str) -> str | None:
    token = uuid4().hex
    if _redis().set(_ENQUEUE_KEY.format(digest=_digest(local_url)), token, nx=True, ex=_ENQUEUE_TTL):
        return token
    return None


def _start_upload(local_url: str, token: str) -> asyncio.Task:
    job = asyncio.create_task(_upload_local_url_background(local_url, token))
    _upload_tasks[local_url] = job
    def finished(done):
        if _upload_tasks.get(local_url) is done:
            _upload_tasks.pop(local_url, None)
        if not done.cancelled():
            done.exception()
    job.add_done_callback(finished)
    return job


def enqueue_oss_upload(local_url: str) -> bool:
    """即时上传和定时补传共享URL互斥；任务强引用保留至线程/回填真正结束。"""
    url = (local_url or "").strip()
    if not storage.is_local_static_url(url) or not oss_svc.oss_enabled() or url in _upload_tasks:
        return False
    token = _claim_upload(url)
    if not token:
        return False
    _start_upload(url, token)
    return True


async def _upload_local_url_background(local_url: str, token: str) -> tuple[bool | None, int]:
    """取消等待者也先等同步线程退出，之后才释放本URL互斥。"""
    worker = asyncio.create_task(asyncio.to_thread(upload_local_url_sync, local_url))
    cancelled = False
    try:
        try:
            oss_url = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    pass
            oss_url = worker.result()
        changed = await backfill_media_url(local_url, oss_url) if oss_url else 0
        if cancelled:
            raise asyncio.CancelledError
        return (True if oss_url else None), changed
    except Exception:  # noqa: BLE001
        logger.exception("background oss upload failed url=%s", local_url)
        return False, 0
    finally:
        await asyncio.to_thread(clear_enqueue_marker, local_url, token)


def clear_enqueue_marker(local_url: str, token: str) -> None:
    try:
        key = _ENQUEUE_KEY.format(digest=_digest(local_url))
        _redis().eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0", 1, key, token)
    except Exception:  # noqa: BLE001
        pass


def start_pending_media_backfill_if_due() -> bool:
    """维护tick只启动/复用唯一批任务，不等待上传，也不受5秒operation取消影响。"""
    global _backfill_task, _next_backfill_at
    if not oss_svc.oss_enabled() or (_backfill_task is not None and not _backfill_task.done()):
        return False
    if time.monotonic() < _next_backfill_at:
        return False
    _next_backfill_at = time.monotonic() + 300
    _backfill_task = asyncio.create_task(backfill_pending_local_media(limit=20), name="oss-media-backfill")
    def finished(done):
        if not done.cancelled() and done.exception() is not None:
            logger.error("OSS补传批次失败", exc_info=done.exception())
    _backfill_task.add_done_callback(finished)
    return True


# 从资产 params 递归收集本地 /static URL
def _collect_local_urls_from_params(params: Any, out: set[str]) -> None:
    if isinstance(params, dict):
        for key, value in params.items():
            if key in _ASSET_PARAM_URL_KEYS and isinstance(value, str):
                raw = value.strip()
                if storage.is_local_static_url(raw):
                    out.add(raw)
            else:
                _collect_local_urls_from_params(value, out)
    elif isinstance(params, list):
        for item in params:
            _collect_local_urls_from_params(item, out)


# 将 params 内 old→new 的本地 URL 替换；有改动返回 True
def _rewrite_local_urls_in_params(params: Any, old: str, new: str) -> bool:
    changed = False
    if isinstance(params, dict):
        for key, value in list(params.items()):
            if isinstance(value, str) and value.strip() == old:
                params[key] = new
                changed = True
            elif isinstance(value, (dict, list)):
                if _rewrite_local_urls_in_params(value, old, new):
                    changed = True
    elif isinstance(params, list):
        for index, item in enumerate(params):
            if isinstance(item, str) and item.strip() == old:
                params[index] = new
                changed = True
            elif isinstance(item, (dict, list)):
                if _rewrite_local_urls_in_params(item, old, new):
                    changed = True
    return changed


async def backfill_media_url(old_url: str, new_url: str) -> int:
    """Replace exact local URL references with OSS URL across media tables."""
    old = (old_url or "").strip()
    new = (new_url or "").strip()
    if not old or not new or old == new:
        return 0

    from app.database import AsyncSessionLocal
    from app.models import Project, Shot, Template, Work
    from app.models_drama import DramaAsset, DramaEpisodeFragment

    targets: list[tuple[type, list[str]]] = [
        (Shot, ["image_url", "video_url", "audio_url"]),
        (Project, ["cover_url", "final_video_url", "ref_image_url"]),
        (Template, ["preview_cover"]),
        (Work, ["cover_url", "video_url"]),
        (DramaAsset, ["cover", "url"]),
        (DramaEpisodeFragment, ["cover", "video"]),
    ]
    changed = 0
    async with AsyncSessionLocal() as db:
        for model, fields in targets:
            for field in fields:
                col = getattr(model, field)
                result = await db.execute(
                    update(model).where(col == old).values(**{field: new})
                )
                changed += int(result.rowcount or 0)

        # 仅当 params JSON 文本含该 URL 时回写（音色 voiceAudio 等）
        assets = (
            await db.execute(
                select(DramaAsset).where(
                    DramaAsset.params.is_not(None),
                    cast(DramaAsset.params, String).like(f"%{old}%"),
                ).with_for_update().execution_options(populate_existing=True)
            )
        ).scalars().all()
        for asset in assets:
            params = asset.params
            if not isinstance(params, dict):
                continue
            rewritten = deepcopy(params)
            if _rewrite_local_urls_in_params(rewritten, old, new):
                asset.params = rewritten
                changed += 1
        await db.commit()
    if changed:
        logger.info("oss backfill %s row(s): %s → %s", changed, old, new)
    return changed


def collect_pending_column_targets() -> list[tuple[type, list[str]]]:
    """补传扫描列：科普只扫成片，不含分镜图/配音/镜头视频。"""
    from app.models import Project, Template, Work
    from app.models_drama import DramaAsset, DramaEpisodeFragment

    return [
        (Project, ["final_video_url"]),
        (Template, ["preview_cover"]),
        (Work, ["cover_url", "video_url"]),
        (DramaAsset, ["cover", "url"]),
        (DramaEpisodeFragment, ["cover", "video"]),
    ]


async def collect_pending_local_media_urls(*, limit: int = 2000) -> list[str]:
    """限定SQL页大小；跨批次按来源、主键、行内URL轮转，不全表all。"""
    global _scan_source, _scan_row, _scan_after_url
    from app.database import AsyncSessionLocal
    from app.models_drama import DramaAsset

    sources = [*collect_pending_column_targets(), (DramaAsset, [])]
    found: set[str] = set()
    budget = max(1, int(limit))
    visited = 0
    async with AsyncSessionLocal() as db:
        while budget > 0 and visited < len(sources):
            model, fields = sources[_scan_source]
            clauses = [getattr(model, field).like("%/static/%") for field in fields]
            if not fields:
                clauses = [cast(model.params, String).like("%/static/%")]
            statement = select(model).where(or_(*clauses)).order_by(model.id).limit(budget)
            if _scan_row is not None:
                statement = statement.where(model.id >= _scan_row if _scan_after_url is not None else model.id > _scan_row)
            rows = list((await db.scalars(statement)).all())
            page_size = budget
            for row in rows:
                urls: set[str] = set()
                if fields:
                    urls.update(raw for field in fields if storage.is_local_static_url(raw := str(getattr(row, field) or "").strip()))
                else:
                    _collect_local_urls_from_params(row.params, urls)
                after = _scan_after_url if row.id == _scan_row else None
                for url in sorted(urls):
                    if after is not None and url <= after:
                        continue
                    found.add(url)
                    if len(found) >= limit:
                        _scan_row, _scan_after_url = row.id, url
                        return sorted(found)
                _scan_row, _scan_after_url = row.id, None
                budget -= 1
            if len(rows) < page_size:
                _scan_source = (_scan_source + 1) % len(sources)
                _scan_row, _scan_after_url = None, None
                visited += 1
            elif not rows:
                break
    return sorted(found)


def upload_local_url_sync(local_url: str) -> str | None:
    """同步上传单个本地 URL；成功返回 OSS https，失败返回 None。"""
    url = (local_url or "").strip()
    if not url or not storage.is_local_static_url(url):
        return None
    if not oss_svc.oss_enabled():
        return None
    path = storage.local_path_from_url(url)
    if not path or not path.is_file():
        logger.warning("oss backfill skip missing file %s", url)
        return None
    oss_url = storage.upload_local_sync(path)
    # 成功须为公网 http(s)；勿用 is_local_static_url（本地副本仍在会误判 OSS URL）
    if not oss_url or not (oss_url.startswith("http://") or oss_url.startswith("https://")):
        raise RuntimeError(f"upload returned local URL for {url}")
    return oss_url


async def backfill_pending_local_media(*, limit: int = 500, dry_run: bool = False) -> dict[str, Any]:
    """小批补传，昂贵上传在线程；成功与失败都保留本地文件。"""
    if not oss_svc.oss_enabled():
        return {"ok": False, "error": "OSS disabled", "pending": 0}
    pending = await collect_pending_local_media_urls(limit=limit)
    if dry_run:
        return {"ok": True, "dry_run": True, "pending": len(pending), "urls": pending[:50]}
    uploaded = changed_rows = failed = skipped = 0
    for local_url in pending:
        if local_url in _upload_tasks:
            skipped += 1
            continue
        try:
            token = await asyncio.to_thread(_claim_upload, local_url)
            if not token:
                skipped += 1
                continue
            job = _start_upload(local_url, token)
            try:
                ok, changed = await asyncio.shield(job)
            except asyncio.CancelledError:
                await job
                raise
            if ok:
                uploaded += 1
                changed_rows += changed
            elif ok is None:
                skipped += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
            logger.exception("oss pending backfill failed")
    return {"ok": failed == 0, "pending": len(pending), "uploaded": uploaded,
            "skipped": skipped, "failed": failed, "changed_rows": changed_rows,
            "errors": ["部分素材补传失败，本地文件已保留，稍后重试"] if failed else []}
