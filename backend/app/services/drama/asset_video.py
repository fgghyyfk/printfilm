"""画布视频资产：按提示词与参考图调用 Seedance 生成成片。"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.services.ark import get_ark
from app.services.drama.billing_util import record_seedance_video_usage, seedance_billing_key
from app.services.drama.build_seedance_generate_body import (
    build_seedance_generate_body,
    build_seedance_reference_catalog,
    drama_asset_to_payload,
)
from app.services.drama.generation import (
    ensure_reference_assets_public_urls,
    extract_asset_ids_from_content,
)
from app.services.drama.image_styles import (
    resolve_image_style_board_url,
)

logger = logging.getLogger(__name__)

# 画布成片下载中断后的自动重试上限（沿原上游任务，不重新生成）
MAX_ASSET_VIDEO_DOWNLOAD_ATTEMPTS = 8
DOWNLOAD_RETRY_MESSAGE = "成片已生成，正在重试下载"


def asset_video_download_retry_payload(
    payload: dict | None,
    *,
    provider_task_id: str,
    max_attempts: int = MAX_ASSET_VIDEO_DOWNLOAD_ATTEMPTS,
) -> dict | None:
    """记下上游任务号并累加重试次数。超过上限返回 None，调用方应失败且不要重新生成。"""
    tid = (provider_task_id or "").strip()
    if not tid:
        return None
    updated = dict(payload or {})
    attempts = int(updated.get("download_attempts") or 0) + 1
    if attempts > max_attempts:
        return None
    updated["provider_task_id"] = tid
    updated["download_attempts"] = attempts
    return updated


def asset_video_download_retry_delay_sec(attempts: int) -> int:
    """下载重试退避秒数，避免立刻打满同一条断开的连接。"""
    return min(180, max(15, 20 * max(1, int(attempts))))


# 合并正文 @asset:id 与显式传入的参考资产 ID（去重、排除自身）
def merge_reference_asset_ids(
    prompt: str,
    extra_ids: list[int] | None,
    *,
    self_asset_id: int,
) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()
    for asset_id in [*extract_asset_ids_from_content(prompt), *(extra_ids or [])]:
        if asset_id == self_asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        merged.append(asset_id)
    return merged


# 按项目加载参考资产，忽略跨项目或已删除的 ID
async def load_reference_assets(
    db: AsyncSession,
    project_id: int,
    asset_ids: list[int],
) -> list[DramaAsset]:
    assets: list[DramaAsset] = []
    for asset_id in asset_ids:
        asset = await db.get(DramaAsset, asset_id)
        if not asset or asset.project_id != project_id:
            continue
        assets.append(asset)
    return assets


async def _download_existing_asset_video(
    provider_task_id: str,
    *,
    project_id: int,
    shot_no: int,
):
    """按已有上游任务号拉成片。未就绪或下载中断时抛出可重试错误，不新建任务。"""
    from app.services.storage import RetryableMediaDownloadError, is_retryable_download_error

    ark = get_ark()
    result = await ark.fetch_task_once(provider_task_id)
    result.provider_task_id = provider_task_id
    if result.status in {"", "running", "queued"}:
        raise RetryableMediaDownloadError(
            "上游成片尚未就绪",
            provider_task_id=provider_task_id,
            counts_attempt=False,
        )
    if result.status != "succeeded" or not result.url:
        raise RuntimeError(result.error or "上游生成失败")
    try:
        local_video, local_last_frame = await ark.save_video_assets_from_result(
            result,
            project_id=project_id,
            shot_no=shot_no,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, RetryableMediaDownloadError):
            if not exc.provider_task_id:
                exc.provider_task_id = provider_task_id
            raise
        if is_retryable_download_error(exc):
            raise RetryableMediaDownloadError(str(exc), provider_task_id=provider_task_id) from exc
        raise
    return local_video, local_last_frame, result


async def generate_asset_video(
    db: AsyncSession,
    user: User,
    project: DramaProject,
    asset: DramaAsset,
    prompt: str,
    *,
    model_id: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration_sec: int | None = None,
    image_style_id: str | None = None,
    reference_asset_ids: list[int] | None = None,
    provider_task_id: str | None = None,
    on_provider_task: Callable[[str], Awaitable[None]] | None = None,
) -> DramaAsset:
    """为画布 video 资产生成 Seedance 视频（不是 Seedream 静帧）。"""
    settings = get_settings()
    ark = get_ark()
    # content 用户提示词（保留 @asset:id 供 Seedance 替换）
    # duration 夹紧到官方时长区间
    # style_id 画面风格
    content = (prompt or "").strip() or "短剧镜头"
    duration = int(duration_sec or 8)
    style_id = (image_style_id or "").strip() or str(
        (project.params or {}).get("image_style_id") or ""
    ).strip() or None
    ratio = (aspect_ratio or "").strip() or "9:16"
    from app.services.media_models.runtime import current_snapshot, operation_for
    if current_snapshot.get():
        operation = operation_for("video", target=asset.id, model=model_id)
        model_id = operation["model_id"]
        duration = operation["params"]["duration"]
        ratio, resolution = operation["params"]["aspect_ratio"], operation["params"]["resolution"]
    from app.services.seedance_resolutions import clamp_video_duration, clamp_video_resolution

    duration = clamp_video_duration(model_id, duration)
    res = clamp_video_resolution(model_id, (resolution or "").strip() or "720p")

    ref_ids = merge_reference_asset_ids(
        content,
        reference_asset_ids,
        self_asset_id=asset.id,
    )
    ref_assets = await load_reference_assets(db, project.id, ref_ids)
    ref_assets = await ensure_reference_assets_public_urls(db, ref_assets, read_only=project.team_id is not None)
    ref_payloads = [drama_asset_to_payload(item) for item in ref_assets]
    # 与正式提交一致：带 script 判定开口角色，才能挂 reference_audio
    catalog = build_seedance_reference_catalog(ref_payloads, script=content)
    board_url = resolve_image_style_board_url(style_id)
    video_board_url = board_url if catalog.images else ""
    logger.info(
        "画布资产生视频参考 project_id=%s asset_id=%s refs=%s images=%s audios=%s",
        project.id,
        asset.id,
        [item.id for item in ref_assets],
        len(catalog.images),
        [a.url[:80] for a in catalog.audios],
    )

    t0 = time.time()
    existing_task_id = (provider_task_id or "").strip()
    if existing_task_id:
        logger.info(
            "画布视频沿原任务重试下载 project_id=%s asset_id=%s provider_task_id=%s",
            project.id,
            asset.id,
            existing_task_id,
        )
        local_video, local_last_frame, task_result = await _download_existing_asset_video(
            existing_task_id,
            project_id=project.id,
            shot_no=asset.id,
        )
        video_url = await _publish_asset_video(
            project,
            asset,
            content,
            local_video,
            local_last_frame,
            model_id=model_id,
            ratio=ratio,
            res=res,
            duration=duration,
            style_id=style_id,
        )
        await _bill_asset_video(
            db,
            user,
            project,
            model_id=model_id,
            duration=duration,
            res=res,
            task_result=task_result,
            settings_model=str(settings.model_video or ""),
        )
        await db.commit()
        await db.refresh(asset)
        logger.info(
            "画布资产生视频完成 project_id=%s asset_id=%s secs=%.1f url=%s refs=%s",
            project.id,
            asset.id,
            time.time() - t0,
            (video_url or "")[:80],
            [item.id for item in ref_assets],
        )
        return asset

    body = build_seedance_generate_body(
        {
            "content": content,
            "reference": ref_payloads,
            "model_id": model_id,
            "video_style_id": style_id,
            "aspect_ratio": ratio,
            "resolution": res,
            "duration_fallback": duration,
            "style_board_url": video_board_url or None,
            # 自由画布资产生视频：不烧模型字幕、不叠人物介绍字卡（后期再叠）
            "burn_subtitles": False,
            "character_intro": False,
        }
    )
    local_video, local_last_frame, task_result = await ark.gen_and_wait_seedance_body(
        body,
        project_id=project.id,
        shot_no=asset.id,
        on_task_created=on_provider_task,
    )
    video_url = await _publish_asset_video(
        project,
        asset,
        content,
        local_video,
        local_last_frame,
        model_id=model_id,
        ratio=ratio,
        res=res,
        duration=duration,
        style_id=style_id,
    )
    await _bill_asset_video(
        db,
        user,
        project,
        model_id=model_id,
        duration=duration,
        res=res,
        task_result=task_result,
        settings_model=str(settings.model_video or ""),
    )
    await db.commit()
    await db.refresh(asset)
    logger.info(
        "画布资产生视频完成 project_id=%s asset_id=%s secs=%.1f url=%s refs=%s",
        project.id,
        asset.id,
        time.time() - t0,
        (video_url or "")[:80],
        [item.id for item in ref_assets],
    )
    return asset


async def _publish_asset_video(
    project: DramaProject,
    asset: DramaAsset,
    content: str,
    local_video: str,
    local_last_frame: str | None,
    *,
    model_id: str | None,
    ratio: str,
    res: str,
    duration: int,
    style_id: str | None,
) -> str:
    """落成片与封面，并写回资产参数。"""
    from app.services import storage as storage_svc

    video_url = storage_svc.republish_url(local_video, sync=True) or local_video
    cover_url = ""
    video_path = storage_svc.local_path_from_url(local_video)
    if video_path is None and isinstance(local_video, str) and not local_video.startswith("http"):
        candidate = Path(local_video)
        if candidate.exists():
            video_path = candidate
    if video_path and video_path.exists():
        from app.services.ffmpeg_compose import extract_video_poster_frame

        poster_dest = storage_svc.project_dir(project.id) / f"asset_{asset.id}_cover.jpg"
        if extract_video_poster_frame(video_path, poster_dest):
            cover_src = storage_svc.rel_static_url(poster_dest)
            cover_url = storage_svc.republish_url(cover_src, sync=True) or cover_src
    if local_last_frame and not cover_url:
        cover_url = storage_svc.republish_url(local_last_frame, sync=True) or local_last_frame

    asset.url = video_url
    asset.cover = cover_url or asset.cover or ""
    asset.asset_type = "video"
    params = dict(asset.params or {})
    params["visualPrompt"] = content
    params["generation"] = {"status": "done"}
    params["videoOptions"] = {
        "model_id": model_id or "seedance-2-5",
        "aspect_ratio": ratio,
        "resolution": res,
        "duration_sec": duration,
        "image_style_id": style_id,
    }
    asset.params = params
    return video_url


async def _bill_asset_video(
    db: AsyncSession,
    user: User,
    project: DramaProject,
    *,
    model_id: str | None,
    duration: int,
    res: str,
    task_result: Any,
    settings_model: str,
) -> None:
    """按本次实际提交的视频模型记一笔用量。"""
    from app.services.tokenfree_pricing import canonicalize_channel_model_id

    raw_billing = (model_id or "").strip()
    if raw_billing:
        billing_model = canonicalize_channel_model_id(raw_billing) or raw_billing
    else:
        billing_model = canonicalize_channel_model_id(settings_model.strip()) or "seedance-2.5"
    await record_seedance_video_usage(
        db,
        user_id=user.id,
        billing_key=seedance_billing_key(generate_audio=True),
        model=billing_model,
        domain="drama",
        task_result=task_result,
        fallback_duration_sec=duration,
        resolution=res,
        provider_task_id=getattr(task_result, "provider_task_id", None),
        drama_project_id=project.id,
    )
