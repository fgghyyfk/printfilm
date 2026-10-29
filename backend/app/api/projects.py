import asyncio
import io
import json
import re
import zipfile
from datetime import datetime

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.deps import get_current_user
from app.models import Project, ProjectStatus, Shot, ShotStatus, UsageEvent, User, Work
from app.models_tasks import TaskRun
from app.schemas import (
    ContentExpandOut,
    ContentExpandRequest,
    ProjectCreate,
    ProjectDownloadRequest,
    ProjectListOut,
    ProjectOut,
    ProjectUpdate,
    ShotOut,
    ShotReorderIn,
    ShotUpdate,
    VoicePreviewOut,
    VoicePreviewRequest,
    WorkOut,
)
from app.schemas_tasks import TaskCreateRequest, TaskTargetBind
from app.services import pipeline, storage
from app.services.bgm import clip_shot_bgm
from app.services.ark import get_ark
from app.services.progress import redis_bridge, subscribe, unsubscribe
from app.services.billing import record_llm_chat_line, run_billed_ephemeral
from app.services.billing.http import http_exception_for_value_error
from app.services.tasks.service import (
    ACTIVE_TASK_STATUSES,
    cancel_tasks_for_scope,
    create_task,
    list_active_tasks_for_owner,
)
from app.services.voices import ensure_voice_preview, list_voices
from app.services.kepu_stages import resolve_kepu_billing_phase
from app.services.kepu_projects import list_project_page, project_runtime_view

router = APIRouter(tags=["projects"])


def _demote_after_edit(project: Project) -> None:
    """改镜头/配乐后作废成片，并按素材把终态打回 script/assets/videos/compose。"""
    project.final_video_url = None
    if project.status not in {
        ProjectStatus.DONE,
        ProjectStatus.AUDITING,
        ProjectStatus.COMPOSING,
        ProjectStatus.VIDEO_READY,
        ProjectStatus.FAILED,
        ProjectStatus.CANCELLED,
    }:
        return
    phase = resolve_kepu_billing_phase(project)
    if phase == "script":
        project.status = ProjectStatus.SCRIPT_READY if project.shots else ProjectStatus.DRAFT
    elif phase == "assets":
        project.status = ProjectStatus.SCRIPT_READY
    elif phase == "videos":
        project.status = ProjectStatus.IMAGE_READY
    else:
        project.status = ProjectStatus.VIDEO_READY


@router.get("/voices")
async def get_voices() -> list[dict]:
    return list_voices()


@router.get("/media-models")
async def get_media_models() -> dict:
    """科普前台可选图/视频模型目录（TokenFree 已勾选模型）。"""
    from app.services.media_catalog import catalog_payload

    return catalog_payload()


@router.post("/voices/preview", response_model=VoicePreviewOut)
async def preview_voice(
    body: VoicePreviewRequest,
    user: User = Depends(get_current_user),
) -> VoicePreviewOut:
    """Generate a short cached TTS sample for audition."""
    _ = user
    try:
        url = await ensure_voice_preview(body.voice_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"试听生成失败：{exc}") from exc
    return VoicePreviewOut(url=url, voice_id=body.voice_id)


@router.post("/content/expand", response_model=ContentExpandOut)
async def expand_content(
    body: ContentExpandRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ContentExpandOut:
    """AI-expand a short topic into a project title + theme brief or full script."""
    topic = (body.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="请输入选题")

    async def _do_expand() -> dict[str, str]:
        try:
            result = await get_ark().expand_content(topic, body.mode)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"AI 生成失败：{exc}") from exc
        await record_llm_chat_line(
            db,
            user_id=user.id,
            domain="kepu",
        )
        return result

    try:
        task, result = await run_billed_ephemeral(
            db,
            user,
            domain="kepu",
            task_type="content_expand",
            executor=_do_expand,
            payload={"mode": body.mode, "topic_len": len(topic)},
            commit=True,
        )
    except ValueError as exc:
        raise http_exception_for_value_error(exc) from exc
    return ContentExpandOut(
        title=result["title"],
        content=result["content"],
        task_id=task.id,
    )


async def _get_owned_project(db: AsyncSession, project_id: int, user: User) -> Project:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.user_id == user.id)
        .options(selectinload(Project.shots))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.active_tasks = await list_active_tasks_for_owner(db, user.id, project_id=project.id)
    runtime_status, runtime_progress, runtime_error = project_runtime_view(project)
    project.status = runtime_status
    project.progress = runtime_progress
    project.error_msg = runtime_error
    return project


def _image_ext_from_magic(raw: bytes) -> str:
    """按文件头识别图片后缀；无法识别则返回空。"""
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return ".gif"
    if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


# 整片级任务占用项目时，禁止再开侧任务（含 cancel_requested，流水线可能仍在写）
_PROJECT_WIDE_TASK_TYPES = {
    "project_pipeline",
    "project_compose_only",
    "project_regen_audio",
    "shot_regen_audio",
}


def _ensure_side_task_allowed(project: Project) -> None:
    """整片任务未终态前拒绝侧任务；单镜重绘可并行，不因残留 IMAGING 卡住。"""
    for task in getattr(project, "active_tasks", []) or []:
        if str(getattr(task, "status", "") or "") not in ACTIVE_TASK_STATUSES:
            continue
        if str(getattr(task, "task_type", "") or "") in _PROJECT_WIDE_TASK_TYPES:
            raise HTTPException(status_code=409, detail="整片生成进行中，请稍后")


# COMPOSING 且无进行中任务时视为拼接已失败卡住，允许重新发起
async def _ensure_compose_allowed(db: AsyncSession, user: User, project: Project) -> None:
    active = await list_active_tasks_for_owner(db, user.id, project_id=project.id)
    if active:
        raise HTTPException(status_code=409, detail="生成进行中，请稍后")
    running = {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.AUDITING,
    }
    if project.status in running:
        raise HTTPException(status_code=409, detail="生成进行中，请稍后")
    # COMPOSING / FAILED / VIDEO_READY / DONE 等均可重试拼接（无 active task）


# 为科普项目/镜头创建统一任务，由平台调度器自动执行
async def _create_kepu_task(
    db: AsyncSession,
    user: User,
    *,
    project_id: int,
    task_type: str,
    shot_id: int | None = None,
    payload: dict | None = None,
) -> int:
    targets = [TaskTargetBind(target_type="project", target_id=project_id)]
    if shot_id is not None:
        targets.append(TaskTargetBind(target_type="shot", target_id=shot_id))
    try:
        task = await create_task(
            db,
            user,
            TaskCreateRequest(
                domain="kepu",
                task_type=task_type,
                dedupe_key=f"kepu:{task_type}:project:{project_id}:shot:{shot_id or 0}",
                payload={"project_id": project_id, "shot_id": shot_id, **(payload or {})},
                project_id=project_id,
                shot_id=shot_id,
                targets=targets,
            ),
        )
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc
    return int(task.id)


@router.post("/projects", response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    from app.models import Template

    tpl = await db.get(Template, body.template_id)
    if not tpl or not tpl.is_active:
        raise HTTPException(status_code=400, detail="无效模板")
    project = Project(
        user_id=user.id,
        template_id=body.template_id,
        title=body.title,
        source_type=body.source_type,
        source_text=body.source_text,
        resolution_mode=body.resolution_mode,
        pipeline_mode=body.pipeline_mode,
        output_ratio=(body.output_ratio or "").strip(),
        voice_id=(body.voice_id or "").strip(),
        # 空则生成时读后台模板；不在创建时拷贝，避免后台改模板对已有项目不生效
        style_prompt=(body.style_prompt or "").strip(),
        character_prompt=(body.character_prompt or "").strip(),
        extra_prompt=(body.extra_prompt or "").strip(),
        image_model=(body.image_model or "").strip(),
        video_model=(body.video_model or "").strip(),
        ref_image_url=body.ref_image_url,
        avatar_engine=(body.avatar_engine or "").strip(),
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    result = await db.execute(
        select(Project).where(Project.id == project.id).options(selectinload(Project.shots))
    )
    return result.scalar_one()


@router.get("/projects", response_model=ProjectListOut)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(8, ge=1, le=50),
    status: str | None = Query(
        None,
        description="all|draft|running|done|published",
    ),
    q: str | None = Query(None, description="title search"),
    pipeline_mode: str | None = Query(
        None,
        description="full|image_text|avatar；空=全部类型",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectListOut:
    """校验分页参数并返回当前用户的项目列表及统计。"""
    return await list_project_page(
        db, user.id, page=page, page_size=page_size,
        status=status, q=q, pipeline_mode=pipeline_mode,
    )


def _safe_zip_name(title: str, project_id: int) -> str:
    raw = (title or "未命名作品").strip() or "未命名作品"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw)
    cleaned = cleaned.strip(" .")[:60] or "未命名作品"
    return f"p{project_id}_{cleaned}.mp4"


@router.post("/projects/download-zip")
async def download_projects_zip(
    body: ProjectDownloadRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Zip final videos for selected owned projects (DONE with final_video_url)."""
    ids = list(dict.fromkeys(int(i) for i in body.ids if int(i) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要下载的作品")
    if len(ids) > 50:
        raise HTTPException(status_code=400, detail="一次最多打包 50 个")

    result = await db.execute(
        select(Project).where(Project.user_id == user.id, Project.id.in_(ids))
    )
    projects = list(result.scalars().all())
    by_id = {p.id: p for p in projects}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"项目不存在: {missing[:5]}")

    entries: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    for pid in ids:
        p = by_id[pid]
        if p.status != ProjectStatus.DONE or not p.final_video_url:
            skipped.append(f"#{pid}")
            continue
        path = storage.local_path_from_url(p.final_video_url)
        try:
            if (not path or not path.exists()) and str(p.final_video_url).startswith("http"):
                dest = storage.project_dir(p.id) / "final.mp4"
                path = await storage.ensure_local_media(p.final_video_url, dest)
        except Exception:  # noqa: BLE001
            path = None
        if not path or not path.exists():
            skipped.append(f"#{pid}")
            continue
        entries.append((_safe_zip_name(p.title, p.id), path.read_bytes()))

    if not entries:
        raise HTTPException(
            status_code=400,
            detail="所选作品暂无成片可下载（需状态为已完成）",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for name, data in entries:
            final_name = name
            n = 1
            while final_name in used:
                stem = name.rsplit(".", 1)[0]
                final_name = f"{stem}_{n}.mp4"
                n += 1
            used.add(final_name)
            zf.writestr(final_name, data)
        if skipped:
            zf.writestr(
                "skipped.txt",
                "以下项目未打包（未完成或缺少成片文件）：\n" + "\n".join(skipped),
            )
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"framecut_videos_{stamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    return await _get_owned_project(db, project_id, user)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_owned_project(db, project_id, user)
    if project.status in {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }:
        raise HTTPException(status_code=409, detail="生成进行中，无法修改")

    data = body.model_dump(exclude_unset=True)
    if "template_id" in data:
        from app.models import Template

        tpl = await db.get(Template, data["template_id"])
        if not tpl or not tpl.is_active:
            raise HTTPException(status_code=400, detail="无效模板")
        # 换模板后清空项目覆盖，后续生成跟随后台模板；请求显式带提示词则保留
        if "style_prompt" not in data:
            data["style_prompt"] = ""
        if "character_prompt" not in data:
            data["character_prompt"] = ""
        if "extra_prompt" not in data:
            data["extra_prompt"] = ""
    if "cover_url" in data and data["cover_url"]:
        url = str(data["cover_url"]).strip()
        if not (url.startswith("/static/") or url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="无效封面地址")
    for key in ("style_prompt", "character_prompt", "extra_prompt", "image_model", "video_model"):
        if key in data:
            data[key] = str(data[key] or "").strip()
    if "image_model" in data and data["image_model"]:
        from app.services.media_catalog import is_valid_project_media_model

        if not is_valid_project_media_model(data["image_model"], "image"):
            raise HTTPException(status_code=400, detail="无效图片模型")
    if "video_model" in data and data["video_model"]:
        from app.services.media_catalog import is_valid_project_media_model

        if not is_valid_project_media_model(data["video_model"], "video"):
            raise HTTPException(status_code=400, detail="无效视频模型")
    if "bgm_lock" in data:
        data["bgm_lock"] = str(data["bgm_lock"] or "").strip()
    if "subtitle_preset" in data:
        data["subtitle_preset"] = str(data["subtitle_preset"] or "").strip()
    if "bgm_lock" in data or "subtitle_preset" in data:
        _demote_after_edit(project)
    for k, v in data.items():
        setattr(project, k, v)
    await db.commit()
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/cover", response_model=ProjectOut)
async def upload_project_cover(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """Upload a custom cover image for the project."""
    project = await _get_owned_project(db, project_id, user)
    if project.status in {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }:
        raise HTTPException(status_code=409, detail="生成进行中，无法更换封面")

    content_type = (file.content_type or "").lower()
    allowed = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = allowed.get(content_type)
    if not ext:
        # Fallback from filename
        suffix = Path(file.filename or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            ext = ".jpg" if suffix == ".jpeg" else suffix
        else:
            raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP / GIF")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="封面不能超过 8MB")

    dest = storage.project_dir(project_id) / f"cover{ext}"
    dest.write_bytes(raw)
    project.cover_url = storage.publish_local(dest)
    await db.commit()
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/generate", response_model=ProjectOut)
async def generate_project(
    project_id: int,
    restart: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """Start or resume the pipeline.

    - First run / restart: script stage only, then pauses at SCRIPT_READY for review.
    - Continue: one billing stage per click — assets → videos → compose（各自预扣）。
    - restart=true: wipe shots/media and regenerate storyboard from scratch.
    """
    project = await _get_owned_project(db, project_id, user)
    if project.status in {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }:
        raise HTTPException(status_code=409, detail="生成进行中，请稍后")

    if restart:
        for shot in list(project.shots):
            await db.delete(shot)
        await db.flush()
        await pipeline.delete_project_assets(project_id)
        project.cover_url = None

    # Resume: clear error, keep existing shots/media (pipeline skips finished stages)
    project.error_msg = None
    project.final_video_url = None
    shots = list(project.shots or [])
    phase = "script" if (restart or not shots) else resolve_kepu_billing_phase(project)
    if phase == "compose":
        # 成片走专用 compose 任务，避免 project_pipeline 重复预扣整片视频
        raise HTTPException(status_code=409, detail="素材已齐，请点击合成成片")
    if restart or not shots:
        # First run / restart — actually splitting storyboard
        project.status = ProjectStatus.SCRIPTING
        project.progress = 1
    elif phase == "assets":
        project.status = ProjectStatus.IMAGING
        project.progress = max(project.progress or 0, 18)
    elif phase == "videos":
        project.status = ProjectStatus.VIDEOING
        project.progress = max(project.progress or 0, 55)
    try:
        await create_task(
            db,
            user,
            TaskCreateRequest(
                domain="kepu",
                task_type="project_pipeline",
                dedupe_key=f"kepu:project_pipeline:{project_id}:{int(bool(restart))}:{phase}",
                payload={
                    "project_id": project_id,
                    "restart": bool(restart),
                    "phase": phase,
                    "pipeline_mode": project.pipeline_mode or "full",
                },
                project_id=project_id,
                targets=[
                    TaskTargetBind(target_type="project", target_id=project_id),
                ],
            ),
        )
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/cancel", response_model=ProjectOut)
async def cancel_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_owned_project(db, project_id, user)
    running = {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }
    if project.status not in running:
        raise HTTPException(status_code=400, detail="当前状态不可取消")

    await cancel_tasks_for_scope(db, user.id, project_id=project_id)
    pipeline.cancel_pipeline(project_id)
    project.status = ProjectStatus.CANCELLED
    project.error_msg = "用户取消"
    await db.commit()
    return await _get_owned_project(db, project_id, user)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Delete a kepu project after detaching billing/task FK rows that block CASCADE."""
    project = await _get_owned_project(db, project_id, user)
    running = {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }
    if project.status in running:
        pipeline.cancel_pipeline(project_id)
        await cancel_tasks_for_scope(db, user.id, project_id=project_id)

    # Keep billing/task history; only clear project FK so DELETE projects can succeed.
    await db.execute(
        update(UsageEvent).where(UsageEvent.project_id == project.id).values(project_id=None)
    )
    await db.execute(
        update(TaskRun).where(TaskRun.project_id == project.id).values(project_id=None)
    )

    # Remove published work if any
    work_result = await db.execute(select(Work).where(Work.project_id == project.id))
    work = work_result.scalar_one_or_none()
    if work:
        await db.delete(work)

    await pipeline.delete_project_assets(project_id)
    await db.delete(project)
    await db.commit()
    return {"ok": True, "id": project_id}


@router.get("/projects/{project_id}/events")
async def project_events(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_project(db, project_id, user)
    queue = subscribe(project_id)
    bridge = asyncio.create_task(redis_bridge(project_id, queue))

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "message", "data": json.dumps(payload, ensure_ascii=False)}
                    if payload.get("event") in {"done", "failed"}:
                        break
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bridge.cancel()
            unsubscribe(project_id, queue)

    return EventSourceResponse(event_generator())


@router.post("/projects/{project_id}/shots", response_model=ShotOut)
async def create_shot(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Shot:
    """在片尾追加一镜空白分镜。"""
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    shots = sorted(project.shots, key=lambda s: s.shot_no)
    next_no = (shots[-1].shot_no + 1) if shots else 1
    from app.services.seedance_segments import SegmentBeat, build_segment_script

    bgm = clip_shot_bgm(getattr(project, "bgm_lock", None))
    visual = "画面轻微动态，保持主体稳定"
    script = build_segment_script(
        [SegmentBeat(duration=4, kind="visual", text=visual)],
        bgm_mood=bgm,
    )
    shot = Shot(
        project_id=project.id,
        shot_no=next_no,
        duration=4,
        narration="",
        overlay_title=f"场景 {next_no:02d}",
        overlay_subtitle="",
        img_prompt=visual,
        video_prompt=script,
        segment_script=script,
        camera="slow pan",
        bgm_mood=bgm,
        status=ShotStatus.PENDING,
    )
    db.add(shot)
    await db.flush()
    await db.refresh(project, attribute_names=["shots"])
    _demote_after_edit(project)
    await db.commit()
    await db.refresh(shot)
    return shot


@router.post("/projects/{project_id}/shots/reorder", response_model=ProjectOut)
async def reorder_shots(
    project_id: int,
    body: ShotReorderIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """按 shot_ids 重排镜号。"""
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    existing = {s.id: s for s in project.shots}
    if set(body.shot_ids) != set(existing.keys()):
        raise HTTPException(status_code=400, detail="镜头列表不完整")
    for i, sid in enumerate(body.shot_ids, start=1):
        existing[sid].shot_no = i
    _demote_after_edit(project)
    await db.commit()
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/shots/{shot_id}/image", response_model=ProjectOut)
async def upload_shot_image(
    project_id: int,
    shot_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """上传替换本镜画面。"""
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(status_code=404, detail="分镜不存在")
    content_type = (file.content_type or "").lower()
    allowed = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = allowed.get(content_type)
    if not ext:
        suffix = Path(file.filename or "").suffix.lower()
        ext = ".jpg" if suffix == ".jpeg" else suffix if suffix in {".jpg", ".png", ".webp", ".gif"} else ""
    if not ext:
        raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP / GIF")
    raw = await file.read(8 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="画面不能超过 8MB")
    sniffed = _image_ext_from_magic(raw)
    if not sniffed:
        raise HTTPException(status_code=400, detail="文件不是有效图片")
    ext = sniffed
    dest = storage.project_dir(project_id) / f"shot_{shot.id}_upload{ext}"
    dest.write_bytes(raw)
    shot.image_url = storage.publish_local(dest)
    shot.image_ark_url = None
    shot.video_url = None
    shot.last_frame_url = None
    shot.status = ShotStatus.IMAGE_READY
    shot.version += 1
    _demote_after_edit(project)
    await db.commit()
    return await _get_owned_project(db, project_id, user)


@router.patch("/projects/{project_id}/shots/{shot_id}", response_model=ShotOut)
async def update_shot(
    project_id: int,
    shot_id: int,
    body: ShotUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Shot:
    from app.models import Template
    from app.services.pipeline import clamp_shot_duration

    project = await _get_owned_project(db, project_id, user)
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(status_code=404, detail="分镜不存在")
    data = body.model_dump(exclude_unset=True)
    if "segment_script" in data and data["segment_script"] is not None:
        from app.services.seedance_segments import (
            apply_segment_script_edit,
            replace_first_visual_in_script,
            replace_narration_in_script,
        )

        bgm = clip_shot_bgm(getattr(project, "bgm_lock", None) or shot.bgm_mood)
        script = str(data["segment_script"])
        # 弹窗旁白/首帧优先写回脚本，避免保存时被旧脚本盖掉
        if "narration" in data and data["narration"] is not None:
            script = replace_narration_in_script(script, str(data["narration"]))
        if "img_prompt" in data and data["img_prompt"] is not None:
            script = replace_first_visual_in_script(script, str(data["img_prompt"]))
        normalized = apply_segment_script_edit(script, bgm_mood=bgm)
        data["segment_script"] = normalized["segment_script"]
        data["duration"] = normalized["duration"]
        data["video_prompt"] = normalized["video_prompt"]
        if normalized.get("narration"):
            data["narration"] = normalized["narration"]
        if normalized.get("img_prompt"):
            data["img_prompt"] = normalized["img_prompt"]
    if "duration" in data and data["duration"] is not None:
        tpl = await db.get(Template, project.template_id)
        data["duration"] = clamp_shot_duration(
            float(data["duration"]),
            pipeline_mode=project.pipeline_mode or "full",
            tpl_min=tpl.shot_duration_min if tpl else 2,
            tpl_max=tpl.shot_duration_max if tpl else 8,
        )
    if "bgm_mood" in data and data["bgm_mood"] is not None:
        data["bgm_mood"] = clip_shot_bgm(str(data["bgm_mood"]))
    # 旁白文本真实变化（直接改旁白或脚本规范化带出的新旁白）时作废整片连贯音轨：
    # 缓存只看文件存在性不比对文本，不删会导致旧配音配新字幕。
    new_narration = data.get("narration")
    audio_dirty = (
        new_narration is not None
        and str(new_narration).strip() != str(shot.narration or "").strip()
    )
    for k, v in data.items():
        setattr(shot, k, v)
    if (project.pipeline_mode or "") == "avatar":
        # 数字人：画面固定是半身照，只有口播变了才需重做该段的配音与对口型视频，其它段不受影响
        if audio_dirty:
            shot.audio_url = None
            shot.video_url = None
            shot.avatar_job_id = None
    # Invalidate downstream if visual prompts changed
    elif "img_prompt" in data:
        shot.image_url = None
        shot.video_url = None
        shot.last_frame_url = None
        shot.video_skip_reason = None  # 画面变更，隐私拦截结论失效
        shot.status = ShotStatus.PENDING
    elif (
        "video_prompt" in data
        or "segment_script" in data
        or "duration" in data
        or "camera" in data
    ):
        shot.video_url = None
        shot.last_frame_url = None
        shot.video_skip_reason = None
    if audio_dirty and (project.pipeline_mode or "") != "avatar":
        full_narration = storage.project_dir(project_id) / "full_narration.mp3"
        full_narration.unlink(missing_ok=True)
        for sibling in project.shots:
            sibling.audio_url = None
    # 统一按素材进度把终态打回对应阶段并作废成片（本端点此前漏调，状态会卡在 DONE）
    _demote_after_edit(project)
    shot.version += 1
    await db.commit()
    await db.refresh(shot)
    return shot


@router.post("/projects/{project_id}/shots/{shot_id}/regen-image", response_model=ProjectOut)
async def regen_image(
    project_id: int,
    shot_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(status_code=404, detail="分镜不存在")
    project.status = ProjectStatus.IMAGING
    project.error_msg = None
    await db.commit()
    await _create_kepu_task(
        db,
        user,
        project_id=project_id,
        shot_id=shot_id,
        task_type="shot_regen_image",
    )
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/shots/{shot_id}/regen-video", response_model=ProjectOut)
async def regen_video(
    project_id: int,
    shot_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    if (project.pipeline_mode or "full") == "image_text":
        raise HTTPException(status_code=400, detail="图文模式无需生成 AI 视频，请直接重新合成成片")
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot or not (shot.image_url or shot.image_ark_url):
        detail = "请先上传数字人照片并完成配音" if (project.pipeline_mode or "") == "avatar" else "请先生成该镜画面"
        raise HTTPException(status_code=400, detail=detail)
    project.status = ProjectStatus.VIDEOING
    project.error_msg = None
    await db.commit()
    await _create_kepu_task(
        db,
        user,
        project_id=project_id,
        shot_id=shot_id,
        task_type="shot_regen_video",
    )
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/shots/{shot_id}/regen-audio", response_model=ProjectOut)
async def regen_audio(
    project_id: int,
    shot_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_owned_project(db, project_id, user)
    _ensure_side_task_allowed(project)
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(status_code=404, detail="分镜不存在")
    project.status = ProjectStatus.AUDIOING
    project.error_msg = None
    await db.commit()
    await _create_kepu_task(
        db,
        user,
        project_id=project_id,
        shot_id=shot_id,
        task_type="shot_regen_audio",
    )
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/regen-audio", response_model=ProjectOut)
async def regen_all_audio(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """Re-TTS all shots with current voice_id, then recompose final video."""
    project = await _get_owned_project(db, project_id, user)
    if project.status in {
        ProjectStatus.SCRIPTING,
        ProjectStatus.IMAGING,
        ProjectStatus.VIDEOING,
        ProjectStatus.AUDIOING,
        ProjectStatus.COMPOSING,
        ProjectStatus.AUDITING,
    }:
        raise HTTPException(status_code=409, detail="生成进行中，请稍后")
    if not project.shots:
        raise HTTPException(status_code=400, detail="暂无分镜，请先生成")
    project.status = ProjectStatus.AUDIOING
    project.progress = 80
    project.error_msg = None
    project.final_video_url = None
    await db.commit()
    await _create_kepu_task(
        db,
        user,
        project_id=project_id,
        task_type="project_regen_audio",
    )
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/compose", response_model=ProjectOut)
async def compose_only(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """Recompose final video from existing images/audio/(videos)."""
    project = await _get_owned_project(db, project_id, user)
    await _ensure_compose_allowed(db, user, project)
    if not project.shots or not (
        any(s.image_url for s in project.shots) or any(s.video_url for s in project.shots)
    ):
        raise HTTPException(status_code=400, detail="缺少分镜图或镜头视频，无法合成")
    project.status = ProjectStatus.COMPOSING
    project.progress = 90
    project.error_msg = None
    await db.commit()
    await _create_kepu_task(
        db,
        user,
        project_id=project_id,
        task_type="project_compose_only",
    )
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/publish", response_model=WorkOut)
async def publish_work(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Work:
    project = await _get_owned_project(db, project_id, user)
    if project.status != ProjectStatus.DONE or not project.final_video_url:
        raise HTTPException(status_code=400, detail="成片未完成，无法发布")
    existing = await db.execute(select(Work).where(Work.project_id == project.id))
    work = existing.scalar_one_or_none()
    if work:
        work.title = project.title
        work.cover_url = project.cover_url
        work.video_url = project.final_video_url
    else:
        work = Work(
            project_id=project.id,
            user_id=user.id,
            title=project.title,
            cover_url=project.cover_url,
            video_url=project.final_video_url,
        )
        db.add(work)
    await db.commit()
    await db.refresh(work)
    return work


@router.get("/works", response_model=list[WorkOut])
async def list_works(db: AsyncSession = Depends(get_db)) -> list[Work]:
    result = await db.execute(
        select(Work).where(Work.visibility == "public").order_by(Work.id.desc()).limit(50)
    )
    return list(result.scalars().all())


@router.get("/quota")
async def quota(user: User = Depends(get_current_user)) -> dict:
    from app.config import get_settings

    settings = get_settings()
    return {
        "quota_left": user.quota_left,
        "quota_enabled": settings.quota_enabled,
        "unlimited": not settings.billing_enabled,
        "billing_enabled": settings.billing_enabled,
        "balance_fen": int(getattr(user, "balance_fen", 0) or 0),
        "frozen_fen": int(getattr(user, "frozen_fen", 0) or 0),
        "balance_yuan": round(int(getattr(user, "balance_fen", 0) or 0) / 100, 2),
        "markup": settings.billing_markup,
    }
