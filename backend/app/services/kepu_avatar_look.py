"""数字人的形象与场景流程：定妆照、口播分段（含场景 + 重点文字）、场景图。提示词见 kepu_avatar_prompts。

流程：上传人脸照 → generate_look() 按风格生成定妆照 →
write_script() 让文本模型按文案风格写分段口播（每段带重点词；场景按「每段换 / 全片固定」给出）→
ensure_scene_images() 以定妆照为参考画场景图（固定场景只画一张，全片共用；「保持原样」沿用原照片场景不画）。

改图 / 文本都走 avatar_ai（OpenAI 兼容，默认 promptoll）；未配置时由调用方回退到原有逻辑。
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, Shot, ShotStatus
from app.services import avatar_ai, storage
from app.services.kepu_avatar_prompts import (  # noqa: F401  旧调用方从这里导入
    DEFAULT_LOOK_STYLE,
    LOOK_STYLES,
    ORIGINAL_LOOK,
    RENDER_STYLES,
    look_prompt,
    look_render,
    normalize_segments,
    scene_prompt,
    script_system,
    script_user_prompt,
    split_highlights,
)

logger = logging.getLogger(__name__)

SCENE_CONCURRENCY = 2
_PAST_ASSETS = {ProjectStatus.VIDEO_READY, ProjectStatus.AUDITING, ProjectStatus.DONE, ProjectStatus.REJECTED,
                ProjectStatus.FAILED}
_PLACEHOLDER_TITLES = {"", "未命名作品"}


def segment_range(source_text: str, source_type: str, max_sec: int) -> tuple[int, int]:
    """段数区间：主题自由创作 4~6 段；已有口播稿按字数估。"""
    from app.services.seedance_segments import suggested_avatar_shot_range

    return (4, 6) if source_type == "theme" else suggested_avatar_shot_range(source_text, max_sec=max_sec)


def shot_scene(seg_scene: str, *, scene_mode: str, look_style: str) -> str:
    """写进分镜的场景描述：「保持原样 + 固定场景」沿用定妆照背景，不再画场景图（返回空）。"""
    if scene_mode == "fixed" and look_style == ORIGINAL_LOOK:
        return ""
    return seg_scene


async def write_script(project_id: int) -> None:
    """数字人模式的写稿阶段：一次请求得到分段口播 + 场景 + 重点词（+ 两行标题），替换项目全部分镜。"""
    from app.config import get_settings
    from app.services.seedance_segments import estimate_narration_duration

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        source_text, source_type = project.source_text, project.source_type
        style, scene_mode = project.avatar_script_style or "kepu", project.avatar_scene_mode or "per_segment"
        look_style = project.avatar_look_style or ""
    max_sec = max(6, int(get_settings().avatar_segment_max_sec))
    data = await avatar_ai.chat_json(script_system(style, scene_mode),
                                     script_user_prompt(source_text, source_type,
                                                        segment_range(source_text, source_type, max_sec)))
    segs = normalize_segments(data)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        for shot in list(project.shots):
            await db.delete(shot)
        await db.flush()
        # 标题：项目标题还是占位（或就是主题原文，批量建项目时如此）才用模型给的
        placeholder = _PLACEHOLDER_TITLES | {source_text.strip()[:60]}
        if source_type == "theme" and data.get("title") and project.title in placeholder:
            project.title = str(data["title"]).strip()[:60]
        if style == "opinion" and not (project.avatar_title2 or "").strip() and data.get("title2"):
            project.avatar_title2 = str(data["title2"]).strip()[:32]
        for i, seg in enumerate(segs, start=1):
            db.add(Shot(
                project_id=project.id, shot_no=i,
                duration=float(min(max_sec, max(3, estimate_narration_duration(seg["narration"])))),
                narration=seg["narration"], overlay_title=seg["title"],
                img_prompt=shot_scene(seg["scene"], scene_mode=scene_mode, look_style=look_style),
                video_prompt="", segment_script="", camera="static", highlights="|".join(seg["keywords"]),
                image_url=project.avatar_image_url, status=ShotStatus.PENDING,
            ))
        project.status = ProjectStatus.SCRIPT_READY
        project.progress = 15
        await db.commit()
    logger.info("avatar script project=%s style=%s scene=%s segments=%d", project_id, style, scene_mode, len(segs))


async def generate_look(project_id: int, style: str, model: str | None = None) -> str:
    """由人脸照生成定妆照，设为项目形象；旧的场景图与对口型视频全部作废。返回新定妆照 URL。"""
    from app.services.kepu_avatar import portrait_path

    style = style if style in LOOK_STYLES else DEFAULT_LOOK_STYLE
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        face_url = project.avatar_face_url or project.avatar_image_url
        if not face_url:
            raise RuntimeError("请先上传人脸照片")
        face = await portrait_path(project, url=face_url)
        model = model or project.avatar_image_model
        framing = project.avatar_framing or "waist"
    dest = storage.project_dir(project_id) / f"avatar_look_{int(time.time())}.png"
    await avatar_ai.edit_image(face, look_prompt(style, framing), dest, model=model)
    url = storage.publish_local(dest)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        project.avatar_image_url = url
        project.avatar_look_style = style
        project.cover_url = url
        project.final_video_url = None
        for shot in project.shots or []:
            shot.image_url = url
            shot.video_url = None
            shot.avatar_job_id = None
            if project.avatar_scene_mode == "fixed" and style == ORIGINAL_LOOK:
                shot.img_prompt = ""
        # 已经出过视频/成片的项目退回「素材就绪」，继续时会重画场景图再对口型
        if project.shots and project.status in _PAST_ASSETS:
            project.status = ProjectStatus.IMAGE_READY
        await db.commit()
    return url


def needs_scene(shot: Shot, look_url: str | None) -> bool:
    """该段是否还要画场景图：有场景描述，且当前画面仍是定妆照占位或文件已丢。"""
    if not (shot.img_prompt or "").strip():
        return False
    url = (shot.image_url or "").strip()
    if not url or url == (look_url or ""):
        return True
    local = storage.local_path_from_url(url)
    return bool(local) and not local.exists()


async def _assign(shot_ids: list[int], url: str) -> None:
    """把场景图写回这些段；新画面作废该段旧视频。"""
    async with AsyncSessionLocal() as db:
        for shot_id in shot_ids:
            row = await db.get(Shot, shot_id)
            if row:
                row.image_url = url
                row.video_url = None
                row.avatar_job_id = None
        await db.commit()


async def ensure_scene_images(project_id: int, look: Path, on_progress=None,
                              only_shot_ids: set[int] | None = None) -> None:
    """为缺场景图的段画「同一个人 + 场景」。

    每段换场景：逐段画，最多并发 SCENE_CONCURRENCY 张；全片固定场景：只画一张，所有缺图的段共用。
    only_shot_ids 给定时只处理这些段（单段重画用）。
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id)
                                  .options(selectinload(Project.shots)))
        project = result.scalar_one()
        model = project.avatar_image_model
        render = look_render(project.avatar_look_style)
        framing = project.avatar_framing or "waist"
        fixed = project.avatar_scene_mode == "fixed"
        todo = [(s.id, s.shot_no, s.img_prompt) for s in sorted(project.shots, key=lambda s: s.shot_no)
                if needs_scene(s, project.avatar_image_url)
                and (only_shot_ids is None or s.id in only_shot_ids)]
    if not todo:
        return

    async def draw(shot_no: int, scene: str) -> str:
        dest = storage.project_dir(project_id) / f"scene_{shot_no:03d}_{int(time.time() * 1000)}.png"
        try:
            await avatar_ai.edit_image(look, scene_prompt(scene, render, framing), dest, model=model)
        except Exception as exc:
            raise RuntimeError(f"第 {shot_no} 段场景图生成失败：{str(exc)[:200]}") from exc
        return storage.publish_local(dest)

    if fixed:
        url = await draw(todo[0][1], todo[0][2])
        await _assign([t[0] for t in todo], url)
        if on_progress:
            await on_progress(len(todo), len(todo), todo[-1][1])
        return

    sem = asyncio.Semaphore(SCENE_CONCURRENCY)
    done = 0

    async def one(shot_id: int, shot_no: int, scene: str) -> None:
        nonlocal done
        async with sem:
            await _assign([shot_id], await draw(shot_no, scene))
            done += 1
            if on_progress:
                await on_progress(done, len(todo), shot_no)

    results = await asyncio.gather(*(one(*t) for t in todo), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        raise errors[0]
