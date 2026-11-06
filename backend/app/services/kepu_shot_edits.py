"""科普分镜保存时，区分画面制作变化与仅旁白/字幕文字变化。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Shot
from app.models_tasks import TaskRun
from app.services.seedance_segments import narration_from_script, seedance_timeline_without_voice


def shot_video_content_changed(shot: Shot, data: dict[str, Any], *, native_audio: bool = False) -> bool:
    """比较保存前后的视频输入；仅外部配音可在旁白改变后保留原视频。"""
    # before / after 与流水线一样优先使用逐段脚本，历史镜头回退 video_prompt。
    before = shot.segment_script or shot.video_prompt or ""
    after = data.get("segment_script", shot.segment_script) or data.get("video_prompt", shot.video_prompt) or ""
    if native_audio and narration_from_script(before) != narration_from_script(after):
        return True
    if seedance_timeline_without_voice(before) != seedance_timeline_without_voice(after):
        return True
    if "duration" in data and data["duration"] != shot.duration:
        return True
    return "camera" in data and str(data["camera"] or "").strip() != str(shot.camera or "").strip()


async def clear_storyboard_rows(db: AsyncSession, project: Project) -> None:
    """新稿写好后替换旧分镜；任务历史解除关联后保留，否则外键会挡住删除。"""
    ids = [shot.id for shot in project.shots]
    if ids:
        await db.execute(update(TaskRun).where(TaskRun.shot_id.in_(ids)).values(shot_id=None))
    for shot in list(project.shots):
        await db.delete(shot)
    await db.flush()
    project.cover_url = None
    project.final_video_url = None
