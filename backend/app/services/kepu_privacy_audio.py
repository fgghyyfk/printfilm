"""内置口播被隐私拦截时，只为静图镜头补本镜旁白。"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, Shot
from app.services import storage
from app.services.ffmpeg_compose import is_near_silent_audio
from app.services.kepu_stages import VIDEO_SKIP_REASON_PRIVACY


def privacy_audio_path(project_id: int, shot, voice: str) -> Path:
    fingerprint = hashlib.sha256(f"{voice}\0{shot.narration or ''}".encode()).hexdigest()[:20]
    return storage.project_dir(project_id) / "kepu" / f"privacy_{shot.id}_{fingerprint}.mp3"


def privacy_audio_ready(project_id: int, shot, voice: str) -> bool:
    if not (shot.narration or "").strip():
        return True
    path = privacy_audio_path(project_id, shot, voice)
    return bool(shot.audio_url and storage.local_path_from_url(shot.audio_url) == path
                and path.is_file() and not is_near_silent_audio(path))


async def ensure_privacy_audio(project_id: int, shot_id: int) -> None:
    from app.services.pipeline import _ensure_not_cancelled, _kepu_seedance_native_audio, _project_voice
    from app.services.ark import get_ark

    async with AsyncSessionLocal() as db:
        project = (await db.execute(select(Project).where(Project.id == project_id)
                   .options(selectinload(Project.template)))).scalar_one()
        shot = await db.get(Shot, shot_id)
        if (not shot or shot.project_id != project_id or not _kepu_seedance_native_audio(project)
                or shot.video_skip_reason != VIDEO_SKIP_REASON_PRIVACY or shot.video_url):
            return
        voice = _project_voice(project)
        if privacy_audio_ready(project_id, shot, voice):
            return
        narration, duration = shot.narration, float(shot.duration or 4)
        dest = privacy_audio_path(project_id, shot, voice)
        shot_no = shot.shot_no

    await _ensure_not_cancelled(project_id)
    result = await get_ark().tts_with_result(
        narration, voice, project_id=project_id,
        # 每次输出隔离，不覆盖普通分镜配音或同号漫剧音色。
        shot_no=int(hashlib.sha256(str(dest).encode()).hexdigest()[:12], 16),
        duration_hint=duration, billing_tokens=get_settings().billing_est_tts_tokens,
        billing_shot_id=shot_id,
    )
    source = storage.local_path_from_url(result.url)
    if not source or not source.is_file() or await asyncio.to_thread(is_near_silent_audio, source):
        raise RuntimeError("隐私镜头备用配音生成失败，请重试配音")
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(dest.write_bytes, source.read_bytes())
    await _ensure_not_cancelled(project_id)
    async with AsyncSessionLocal() as db:
        project = (await db.execute(select(Project).where(Project.id == project_id).with_for_update()
                   .options(selectinload(Project.template)))).scalar_one()
        shot = await db.get(Shot, shot_id)
        if (project.status == ProjectStatus.CANCELLED or not shot or shot.narration != narration or _project_voice(project) != voice
                or shot.video_skip_reason != VIDEO_SKIP_REASON_PRIVACY or shot.video_url):
            raise RuntimeError("镜头内容已更新，备用配音未写回，请刷新后继续")
        shot.audio_url = storage.publish_local(dest)
        await db.commit()
