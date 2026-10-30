"""数字人批量生产：把一个调好的数字人项目（形象、音色、场景、构图、文案风格…）套到多个选题上，全自动出片。

- create_batch()：按选题逐个复制项目设置（定妆照/人脸照复制进新项目目录，互不影响），并提交 kepu/avatar_auto 任务
- run_avatar_auto()：avatar_auto 任务的执行体，写稿 → 场景图 + 配音 → 逐段对口型 → 合成，中途不停顿
GPU 服务本身串行排队，多个项目同时提交也只是排队，不会抢显卡。
"""
from __future__ import annotations

import logging
import shutil

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import Project, ProjectStatus, User
from app.services import storage

logger = logging.getLogger(__name__)

AUTO_TASK_TYPE = "avatar_auto"
MAX_BATCH = 50
# 新项目从源项目继承的设置
_COPY_FIELDS = (
    "template_id", "pipeline_mode", "output_ratio", "voice_id", "resolution_mode", "subtitle_preset", "bgm_lock",
    "style_prompt", "character_prompt", "extra_prompt", "avatar_engine", "avatar_look_style", "avatar_image_model",
    "avatar_scene_mode", "avatar_framing", "avatar_script_style", "avatar_resolution",
)


def clean_topics(raw: list[str]) -> list[str]:
    """清洗选题：去空、去重、单个最长 2000 字，最多 MAX_BATCH 个。"""
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()[:2000]
        if len(text) >= 2 and text not in out:
            out.append(text)
    return out[:MAX_BATCH]


def _copy_media(url: str | None, project_id: int, name: str) -> str | None:
    """把源项目的本地图片复制进新项目目录（源项目删除时不受影响）；非本地地址原样沿用。"""
    if not url:
        return url
    local = storage.local_path_from_url(url)
    if not local or not local.exists():
        return url
    dest = storage.project_dir(project_id) / f"{name}{local.suffix}"
    shutil.copy(local, dest)
    return storage.publish_local(dest)


async def clone_for_topic(db: AsyncSession, src: Project, topic: str, source_type: str) -> Project:
    """按源项目设置新建一个数字人项目（标题先用选题占位，写稿时由模型给的标题替换）。"""
    project = Project(user_id=src.user_id, title=topic[:60], source_type=source_type, source_text=topic,
                      status=ProjectStatus.DRAFT, avatar_title2="")
    for field in _COPY_FIELDS:
        setattr(project, field, getattr(src, field))
    db.add(project)
    await db.flush()
    look_same_as_face = (src.avatar_image_url or "") == (src.avatar_face_url or src.avatar_image_url or "")
    project.avatar_face_url = _copy_media(src.avatar_face_url or src.avatar_image_url, project.id, "avatar_face")
    project.avatar_image_url = project.avatar_face_url if look_same_as_face \
        else _copy_media(src.avatar_image_url, project.id, "avatar_look")
    project.cover_url = project.avatar_image_url
    return project


async def create_batch(db: AsyncSession, user: User, src: Project, topics: list[str],
                       source_type: str = "theme") -> list[Project]:
    """为每个选题建项目并提交全自动任务；返回新项目列表。"""
    from app.schemas_tasks import TaskCreateRequest, TaskTargetBind
    from app.services.tasks.service import create_task

    if (src.pipeline_mode or "") != "avatar":
        raise ValueError("只有数字人口播项目可以批量生产")
    if not (src.avatar_image_url or src.avatar_face_url):
        raise ValueError("源项目还没有数字人照片")
    from app.services.kepu_avatar_billing import ensure_batch_balance

    topics = clean_topics(topics)
    # 先按整批估价校验余额，避免建到一半才发现钱不够
    await ensure_batch_balance(db, user, src, topics, source_type)
    created: list[Project] = []
    for topic in topics:
        project = await clone_for_topic(db, src, topic, source_type)
        project.status = ProjectStatus.SCRIPTING
        project.progress = 1
        await db.commit()
        await create_task(db, user, TaskCreateRequest(
            domain="kepu", task_type=AUTO_TASK_TYPE, dedupe_key=f"kepu:{AUTO_TASK_TYPE}:{project.id}",
            payload={"project_id": project.id, "pipeline_mode": "avatar", "batch_source": src.id},
            project_id=project.id, targets=[TaskTargetBind(target_type="project", target_id=project.id)],
        ))
        created.append(project)
    logger.info("avatar batch src=%s created=%s", src.id, [p.id for p in created])
    return created


async def _mark(project_id: int, status: str, error: str | None = None, progress: int | None = None) -> None:
    """落项目状态（已取消的不覆盖）。"""
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if not project or (project.status == ProjectStatus.CANCELLED and status != ProjectStatus.CANCELLED):
            return
        project.status = status
        project.error_msg = error
        if progress is not None:
            project.progress = progress
        await db.commit()


async def run_avatar_auto(project_id: int) -> None:
    """全自动出片：写稿 → 场景图 + 配音 → 逐段对口型 → 合成。失败落 FAILED 并带中文原因，可在页面上继续。"""
    from app.services.kepu_avatar import generate_videos, prepare_assets
    from app.services.pipeline import PipelineCancelled, _compose_stage, _ensure_not_cancelled, _script_stage
    from app.services.progress import publish_progress

    from sqlalchemy import func, select

    from app.models import Shot

    try:
        await _ensure_not_cancelled(project_id)
        async with AsyncSessionLocal() as db:
            has_script = (await db.scalar(select(func.count()).select_from(Shot)
                                          .where(Shot.project_id == project_id))) or 0
        # 重试时已有分镜就沿用（不重写文案）；已完成的配音/视频段各阶段会自动跳过
        if not has_script:
            await _script_stage(project_id)
        await _ensure_not_cancelled(project_id)
        await prepare_assets(project_id)
        await _ensure_not_cancelled(project_id)
        await generate_videos(project_id)
        await _ensure_not_cancelled(project_id)
        await _compose_stage(project_id)
        await _mark(project_id, ProjectStatus.DONE, None, 100)
        await publish_progress(project_id, {"event": "done", "percent": 100})
    except PipelineCancelled:
        await _mark(project_id, ProjectStatus.CANCELLED, "用户取消")
        raise
    except Exception as exc:
        logger.exception("avatar auto failed project=%s", project_id)
        await _mark(project_id, ProjectStatus.FAILED, f"自动出片失败：{str(exc)[:300]}")
        await publish_progress(project_id, {"event": "failed", "message": str(exc)[:300], "retryable": True})
        raise
