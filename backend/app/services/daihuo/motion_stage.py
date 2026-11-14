"""带货动态镜头在视频阶段的准备：满幅首帧（必要时扩图）→ Seedance 请求参数；失败降级为静图镜头。"""

from __future__ import annotations

import logging
from typing import Any

from app.services.daihuo.motion import DEFAULT_ACTION, build_video_prompt, ensure_motion_frame, video_kwargs
from app.services.kepu_stages import VIDEO_SKIP_REASON_STATIC

logger = logging.getLogger(__name__)


async def daihuo_shot_request(project_id: int, meta: dict[str, Any], *, ratio: str) -> dict[str, Any] | None:
    """返回 generate_shot_video 所需参数；首帧准备失败时把该镜标成静图并返回 None。"""
    from app.services.progress import publish_progress

    try:
        image_url = await ensure_motion_frame(project_id, int(meta["id"]), ratio=ratio)
    except Exception as exc:  # noqa: BLE001 — 扩图失败不拖垮整片，退回静图推拉
        logger.warning("daihuo motion frame failed project=%s shot=%s err=%s", project_id, meta["id"], exc)
        await mark_static(int(meta["id"]))
        await publish_progress(project_id, {
            "event": "progress", "stage": "VIDEOING", "shot": meta["shot_no"],
            "message": f"镜头 {meta['shot_no']} 首帧扩图失败，已改用静图推拉",
        })
        return None
    prompt = (meta.get("video_prompt") or "").strip() or build_video_prompt(DEFAULT_ACTION)
    return video_kwargs(image_url=image_url, prompt=prompt, duration=float(meta.get("duration") or 4))


async def mark_static(shot_id: int) -> None:
    """把镜头标成静图推拉（不再提交视频）。"""
    from app.database import AsyncSessionLocal
    from app.models import Shot

    async with AsyncSessionLocal() as db:
        shot = await db.get(Shot, shot_id)
        if shot is not None:
            shot.video_skip_reason = VIDEO_SKIP_REASON_STATIC
            await db.commit()
