"""科普「数字人口播」模式（pipeline_mode=avatar）的素材与视频阶段。

- assets：每段口播单独配音（口型对齐该段音频），段时长取配音实测时长；分镜图统一用项目半身照
- videos：按段提交到自建 GPU 服务（照片 + 该段音频 → 对口型视频），逐段等待、下载回本地
- 合成阶段复用 pipeline._compose_stage，参数见 avatar_compose_options()

GPU 任务 id 记在 shots.avatar_job_id：流水线中断重启后继续等同一个任务，不重复占用 GPU。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, Shot, ShotStatus
from app.services import avatar_video, storage
from app.services.ffmpeg_compose import is_near_silent_audio, probe_duration
from app.services.progress import publish_progress

logger = logging.getLogger(__name__)

# 对口型模型的提示词只描述「人在说话」，内容由音频驱动
AVATAR_PROMPT = ("A person is talking to the camera naturally, with subtle head movements and natural facial "
                 "expressions, upper body, steady camera.")


async def _load_project(db, project_id: int) -> Project:
    """带分镜加载项目。"""
    result = await db.execute(
        select(Project).where(Project.id == project_id).options(selectinload(Project.shots), selectinload(Project.template))
    )
    return result.scalar_one()


async def portrait_path(project: Project, *, url: str | None = None) -> Path:
    """图片的本地路径（默认项目定妆照 avatar_image_url）；未上传时抛错，OSS 地址先下载到本地。"""
    url = (url if url is not None else project.avatar_image_url or "").strip()
    if not url:
        raise RuntimeError("请先上传数字人照片")
    local = storage.local_path_from_url(url)
    if local and local.exists():
        return local
    if url.startswith("http"):
        name = Path(url.split("?", 1)[0]).name or "avatar_portrait.png"
        return await storage.ensure_local_media(url, storage.project_dir(project.id) / f"dl_{name}")
    raise RuntimeError("数字人照片文件不存在，请重新上传")


async def shot_image_path(project: Project, shot: Shot) -> Path:
    """该段对口型用的画面：有场景图用场景图，否则用定妆照。"""
    if (shot.image_url or "").strip():
        try:
            return await portrait_path(project, url=shot.image_url)
        except RuntimeError:
            logger.warning("avatar shot image missing project=%s shot=%s, fallback to look", project.id, shot.shot_no)
    return await portrait_path(project)


async def prepare_assets(project_id: int) -> None:
    """逐段配音并把半身照设为每段分镜图；已有可用配音的段跳过。"""
    from app.services.pipeline import _ensure_not_cancelled, _project_voice, _record_usage_est, _set_status
    from app.services.kepu_stages import shot_audio_file_ok

    from app.services import avatar_ai
    from app.services.kepu_avatar_look import ensure_scene_images

    await _set_status(project_id, ProjectStatus.IMAGING, 18, "AVATAR_TTS")
    async with AsyncSessionLocal() as db:
        project = await _load_project(db, project_id)
        look = await portrait_path(project)
        voice = _project_voice(project)
        portrait_url = project.avatar_image_url
        shots = sorted(project.shots, key=lambda s: s.shot_no)
        todo = [(s.id, s.shot_no, (s.narration or "").strip()) for s in shots if not shot_audio_file_ok(s)]
        for s in shots:
            # 没有场景图的段先用定妆照占位（配了改图服务时下面会逐段重画）
            if not (s.image_url or "").strip():
                s.image_url = portrait_url
            if s.status == ShotStatus.PENDING:
                s.status = ShotStatus.IMAGE_READY
        await db.commit()

    if avatar_ai.is_configured():
        async def scene_progress(done: int, total: int, shot_no: int) -> None:
            await publish_progress(project_id, {"event": "progress", "stage": "AVATAR_SCENE",
                                                "percent": 18 + int(15 * done / max(1, total)),
                                                "message": f"场景图 {done}/{total}（第 {shot_no} 段完成）"})

        await _ensure_not_cancelled(project_id)
        await publish_progress(project_id, {"event": "progress", "stage": "AVATAR_SCENE", "percent": 18,
                                            "message": "按每段内容生成场景图"})
        await ensure_scene_images(project_id, look, on_progress=scene_progress)

    from app.services.ark import get_ark

    ark = get_ark()
    for i, (shot_id, shot_no, text) in enumerate(todo, start=1):
        await _ensure_not_cancelled(project_id)
        if not text:
            raise RuntimeError(f"第 {shot_no} 段没有口播文案，请补充后再继续")
        await publish_progress(project_id, {"event": "progress", "stage": "AVATAR_TTS",
                                            "percent": 33 + int(15 * i / max(1, len(todo))),
                                            "message": f"配音第 {shot_no} 段（{i}/{len(todo)}）"})
        url = await ark.tts(text, voice, project_id=project_id, shot_no=shot_no,
                            duration_hint=max(3.0, len(text) / 4.5))
        path = storage.local_path_from_url(url)
        if not path or not path.exists() or is_near_silent_audio(path):
            raise RuntimeError(f"第 {shot_no} 段配音失败（无声音），请稍后重试")
        seconds = probe_duration(path) or 0.0
        await _record_usage_est(project_id, "tts", shot_id=shot_id)
        async with AsyncSessionLocal() as db:
            row = await db.get(Shot, shot_id)
            if row:
                row.audio_url = url
                row.duration = round(float(seconds), 2) if seconds > 0 else row.duration
                # 配音变了，旧的对口型视频作废
                row.video_url = None
                row.avatar_job_id = None
                row.status = ShotStatus.AUDIO_READY
                await db.commit()
    await _set_stage_done(project_id, ProjectStatus.IMAGE_READY, 50)


async def generate_videos(project_id: int) -> None:
    """逐段生成对口型视频；某段失败即停止并报错（已完成的段保留，重试只补缺的段）。"""
    from app.services.kepu_stages import shot_video_ready
    from app.services.pipeline import _ensure_not_cancelled, _set_status

    await _set_status(project_id, ProjectStatus.VIDEOING, 55, "AVATAR_VIDEO")
    async with AsyncSessionLocal() as db:
        project = await _load_project(db, project_id)
        image = await portrait_path(project)
        engine = avatar_video.resolve_engine(project.avatar_engine)
        todo = [s.id for s in sorted(project.shots, key=lambda s: s.shot_no) if not shot_video_ready(s)]
        # 重试时清掉上次失败留下的报错，避免成功后页面仍显示旧错误
        project.error_msg = None
        await db.commit()
    # 换过定妆照/场景描述后可能还有段没画场景图：先补齐，避免拿占位定妆照去对口型
    from app.services import avatar_ai
    from app.services.kepu_avatar_look import ensure_scene_images

    if avatar_ai.is_configured():
        await ensure_scene_images(project_id, image)
        async with AsyncSessionLocal() as db:
            project = await _load_project(db, project_id)
            todo = [s.id for s in sorted(project.shots, key=lambda s: s.shot_no) if not shot_video_ready(s)]
    total = len(todo)
    for i, shot_id in enumerate(todo, start=1):
        await _ensure_not_cancelled(project_id)
        await _one_segment(project_id, shot_id, engine=engine, index=i, total=total)
    await _set_stage_done(project_id, ProjectStatus.VIDEO_READY, 88)


async def _set_stage_done(project_id: int, status: str, progress: int) -> None:
    """阶段跑完后落到对应就绪态（与 pipeline 原有素材/视频阶段一致），页面才会显示「待继续」。"""
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project and project.status != ProjectStatus.CANCELLED:
            project.status = status
            project.progress = progress
            if not project.cover_url:
                project.cover_url = project.avatar_image_url
            await db.commit()


async def _one_segment(project_id: int, shot_id: int, *, engine: str, index: int, total: int) -> None:
    """提交（或接续）单段 GPU 任务（画面用该段场景图），等待完成后下载并回写分镜。"""
    from app.services.pipeline import PipelineCancelled, _ensure_not_cancelled

    async with AsyncSessionLocal() as db:
        shot = await db.get(Shot, shot_id)
        audio = storage.local_path_from_url(shot.audio_url or "") if shot else None
        if not shot or not audio or not audio.exists():
            raise RuntimeError(f"第 {shot.shot_no if shot else '?'} 段缺少配音，请先重新生成配音")
        project = await db.get(Project, project_id)
        image = await shot_image_path(project, shot)
        resolution = (project.avatar_resolution or "").strip() or None
        shot_no, job_id = shot.shot_no, shot.avatar_job_id
        if not job_id:
            job_id = await avatar_video.submit(image, audio, engine=engine, prompt=AVATAR_PROMPT,
                                               resolution=resolution)
            shot.avatar_job_id = job_id
            await db.commit()
            logger.info("avatar submit project=%s shot=%s engine=%s job=%s", project_id, shot_no, engine, job_id)

    base = 55 + int(33 * (index - 1) / max(1, total))
    span = 33 / max(1, total)
    last_db = 0.0

    async def on_progress(st: avatar_video.AvatarJobStatus) -> None:
        nonlocal last_db
        try:
            await _ensure_not_cancelled(project_id)
        except PipelineCancelled:
            await avatar_video.cancel(job_id)
            raise
        if st.status == "queued":
            msg = f"第 {shot_no} 段排队中（前面还有 {max(0, (st.queue_position or 1) - 1)} 个）"
        elif st.status == "loading":
            msg = f"GPU 正在加载数字人模型（首次约 15 分钟），第 {shot_no} 段随后开始"
        else:
            msg = f"生成数字人第 {shot_no} 段（{index}/{total}）：{st.message or '进行中'}"
        percent = base + int(span * max(0.0, min(1.0, st.progress)))
        await publish_progress(project_id, {"event": "progress", "stage": "AVATAR_VIDEO", "percent": percent,
                                            "message": msg})
        # 进度写库不必每次（前端轮询项目时能看到大致进度）
        if time.monotonic() - last_db > 120:
            last_db = time.monotonic()
            async with AsyncSessionLocal() as db2:
                p = await db2.get(Project, project_id)
                if p:
                    p.progress = percent
                    await db2.commit()

    st = await avatar_video.wait(job_id, on_progress=on_progress)
    if st.status != "succeeded":
        async with AsyncSessionLocal() as db:
            row = await db.get(Shot, shot_id)
            if row:
                row.avatar_job_id = None
                await db.commit()
        raise RuntimeError(f"数字人第 {shot_no} 段生成失败：{(st.error or '未知错误')[:200]}")

    dest = storage.project_dir(project_id) / f"shot_{shot_no:03d}_avatar_{int(time.time())}.mp4"
    await avatar_video.download(job_id, dest, image=image, audio=audio)
    seconds = probe_duration(dest) or 0.0
    url = storage.publish_local(dest)
    async with AsyncSessionLocal() as db:
        row = await db.get(Shot, shot_id)
        if row:
            row.video_url = url
            row.avatar_job_id = None
            row.status = ShotStatus.VIDEO_READY
            await db.commit()
    await _record_avatar_usage(project_id, shot_id, seconds)
    logger.info("avatar done project=%s shot=%s seconds=%.1f", project_id, shot_no, seconds)


async def regen_segment_video(project_id: int, shot_id: int) -> None:
    """单段重新生成对口型视频（沿用该段现有配音）。"""
    async with AsyncSessionLocal() as db:
        project = await _load_project(db, project_id)
        engine = avatar_video.resolve_engine(project.avatar_engine)
        shot = next((s for s in project.shots if s.id == shot_id), None)
        if not shot:
            raise ValueError("分镜不存在")
        shot.video_url = None
        shot.avatar_job_id = None
        await db.commit()
    await _one_segment(project_id, shot_id, engine=engine, index=1, total=1)
    await _after_regen(project_id)


async def regen_segment_audio(project_id: int, shot_id: int) -> None:
    """单段重新配音；该段旧视频作废，需再生成视频后才能合成。"""
    async with AsyncSessionLocal() as db:
        shot = await db.get(Shot, shot_id)
        if not shot or shot.project_id != project_id:
            raise ValueError("分镜不存在")
        if not (shot.narration or "").strip():
            raise ValueError("该段口播为空，无法配音")
        shot.audio_url = None
        await db.commit()
    await prepare_assets(project_id)
    await _after_regen(project_id)


async def _after_regen(project_id: int) -> None:
    """单段重做后：清掉旧成片，按各段视频是否齐全回到对应状态。"""
    async with AsyncSessionLocal() as db:
        project = await _load_project(db, project_id)
        if project.status == ProjectStatus.CANCELLED:
            return
        project.final_video_url = None
        shots = list(project.shots or [])
        project.status = ProjectStatus.VIDEO_READY if shots and all(s.video_url for s in shots) \
            else ProjectStatus.IMAGE_READY
        await db.commit()


async def _record_avatar_usage(project_id: int, shot_id: int, seconds: float) -> None:
    """按成片秒数记账（后台单价为 0 时不记）。"""
    price = int(get_settings().avatar_price_fen_per_sec or 0)
    if price <= 0 or seconds <= 0:
        return
    try:
        from app.services.billing import record_line

        async with AsyncSessionLocal() as db:
            project = await db.get(Project, project_id)
            if not project:
                return
            await record_line(db, user_id=project.user_id, billing_key="avatar_video", provider="avatar",
                              raw={"cost_fen": int(round(seconds * price)), "seconds": round(seconds, 2)},
                              project_id=project_id, shot_id=shot_id, domain="kepu")
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("avatar billing record failed project=%s shot=%s", project_id, shot_id)


def estimate_videos_fen(project: Project) -> int:
    """视频阶段预扣：未完成段的配音秒数 × 单价（无配音时按 15 秒/段估）。"""
    from app.services.kepu_stages import shot_video_ready

    price = int(get_settings().avatar_price_fen_per_sec or 0)
    if price <= 0:
        return 0
    secs = sum(float(s.duration or 15.0) for s in (project.shots or []) if not shot_video_ready(s))
    return int(round(secs * price))
