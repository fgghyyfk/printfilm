"""Independent video, image and audio generation and task history."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from PIL import Image
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import ToolRun, User
from app.models_tasks import TaskRun
from app.schemas_tools import ToolRunListOut, ToolRunOut, ToolRunRecordOut, ToolTaskOut
from app.services.billing import settle_deferred_video_poll
from app.services.billing.http import http_exception_for_value_error
from app.services.studio_generation import enqueue_generation, sync_generation_record, validate_generation
from app.services.studio_tools import get_tool_run, poll_image_tool_task, poll_video_task, save_upload, update_tool_run_task

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/config")
async def generation_config(user: User = Depends(get_current_user)) -> dict:
    from app.config import get_settings
    settings = get_settings()
    return {"models": {"video": settings.model_video, "image": settings.model_image,
                       "audio": settings.model_audio}}


@router.post("/run", response_model=ToolRunOut)
async def run_tool(
    tool_id: str = Form(..., max_length=16), prompt: str = Form("", max_length=7000),
    negative: str = Form("", max_length=2000), ratio: str = Form("", max_length=16),
    strength: str = Form("", max_length=16), mode: str = Form("", max_length=32),
    pack: str = Form("", max_length=32), duration: str = Form("", max_length=16),
    motion: str = Form("", max_length=16), voice: str = Form("narrator_calm", max_length=64),
    model_id: str = Form("", max_length=128), resolution: str = Form("", max_length=16),
    files: list[UploadFile] | None = File(default=None),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> ToolRunOut:
    tid = tool_id.strip()
    params = dict(negative=negative, ratio=ratio, strength=strength, mode=mode, pack=pack,
                  duration=duration, motion=motion, voice=voice, model_id=model_id, resolution=resolution)
    saved = []
    try:
        validate_generation(tid, prompt, params, len(files or []))
        for item in files or []:
            raw = await item.read(40 * 1024 * 1024 + 1)
            if not raw or len(raw) > 40 * 1024 * 1024:
                raise ValueError("文件不能为空，且单个文件不能超过 40MB")
            name = item.filename or "upload.png"
            suffix = name.rsplit(".", 1)[-1].lower()
            if suffix in {"mp4", "mov", "webm"} and tid == "v2v":
                pass
            elif suffix in {"jpg", "jpeg", "png", "webp"} and tid != "audio":
                try:
                    with Image.open(BytesIO(raw)) as image:
                        image.verify()
                except Exception as exc:
                    raise ValueError("参考图文件无效") from exc
            else:
                raise ValueError("请上传 JPG、PNG、WebP 图片，视频参考支持 MP4、MOV、WebM")
            saved.append(save_upload(user.id, raw, name))
        data = await enqueue_generation(db, user, tool_id=tid, prompt=prompt, params=params, files=saved)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        for path in saved:
            path.unlink(missing_ok=True)
        raise http_exception_for_value_error(exc) from exc
    except Exception:
        await db.rollback()
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    return ToolRunOut.model_validate(data)


@router.get("/tasks/{task_id}", response_model=ToolTaskOut)
async def get_tool_task(task_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> ToolTaskOut:
    row = (await db.execute(select(ToolRun).where(ToolRun.user_id == user.id, ToolRun.task_id == task_id).limit(1))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = await sync_generation_record(db, row)
    if task:
        data = dict(status=row.status, kind=row.kind, urls=list(row.urls or []), error=row.error,
                    platform_task_id=task.id, provider_task_id=task.provider_task_id,
                    queue_type=task.queue_type, preview_url=row.preview_url)
    elif row.status in {"succeeded", "failed", "cancelled"}:
        data = dict(status=row.status, kind=row.kind, urls=list(row.urls or []), error=row.error)
    elif row.kind == "image":
        data = await poll_image_tool_task(db, user, task_id)
    else:
        # Compatibility for pre-migration provider IDs; new tasks are polled by the server.
        data = await poll_video_task(user, task_id)
        await settle_deferred_video_poll(db, user, provider_task_id=task_id, poll_status=data["status"],
            error=data.get("error"), billing_task_id=(row.params or {}).get("billing_task_id"),
            usage_tokens=int((data.get("usage") or {}).get("total_tokens") or 0),
            completion_tokens=int((data.get("usage") or {}).get("completion_tokens") or 0), raw_usage=data.get("raw_usage"))
        await update_tool_run_task(db, user.id, task_id, data)
    await db.commit()
    return ToolTaskOut.model_validate(data)


def _record_out(row: ToolRun, task: TaskRun | None = None) -> ToolRunRecordOut:
    params = {key: value for key, value in (row.params or {}).items() if key != "file_paths"}
    result = (task.result_payload or {}) if task else {}
    status = {"pending": "queued", "leased": "queued", "awaiting_poll": "running"}.get(task.status, task.status) if task else row.status
    return ToolRunRecordOut(
        id=row.id, tool_id=row.tool_id, kind=row.kind, status=status, prompt=row.prompt or "",
        preview_url=row.preview_url, urls=list(result.get("urls") or row.urls or []), task_id=row.task_id,
        params=params, error=task.error_message if task else row.error,
        created_at=row.created_at.isoformat() if row.created_at else "",
        platform_task_id=task.id if task else None, provider_task_id=task.provider_task_id if task else params.get("provider_task_id"),
        queue_type=task.queue_type if task else None, provider_name=task.provider_name if task else None,
        provider_status=task.provider_status if task else None,
        started_at=task.started_at if task else None, finished_at=task.finished_at if task else None,
        billing_charged_fen=task.billing_charged_fen if task else 0,
    )


@router.get("/runs", response_model=ToolRunListOut)
async def list_runs(
    page: int = Query(1, ge=1), page_size: int = Query(8, ge=1, le=50),
    kind: str | None = Query(None, pattern="^(video|image|audio)$"),
    status: str | None = Query(None, pattern="^(processing|succeeded|failed|cancelled)$"),
    task_id: str | None = Query(None, max_length=128),
    created_from: datetime | None = None, created_to: datetime | None = None,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> ToolRunListOut:
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=400, detail="开始时间不能晚于结束时间")
    task_ref = func.coalesce(ToolRun.params["platform_task_id"].as_integer(), ToolRun.params["billing_task_id"].as_integer())
    join = (TaskRun.id == task_ref) & (TaskRun.requested_by == ToolRun.user_id)
    filters = [ToolRun.user_id == user.id]
    if kind:
        filters.append(ToolRun.kind == kind)
    if task_id:
        value = task_id.strip()
        filters.append(or_(ToolRun.task_id == value, TaskRun.provider_task_id == value,
                           cast(TaskRun.id, String) == value, cast(ToolRun.id, String) == value))
    if created_from:
        filters.append(ToolRun.created_at >= created_from)
    if created_to:
        filters.append(ToolRun.created_at <= created_to)
    effective = func.coalesce(TaskRun.status, ToolRun.status)
    bucket = case((effective.in_(["succeeded", "failed", "cancelled"]), effective), else_="processing")
    counts = dict((await db.execute(select(bucket, func.count()).select_from(ToolRun).outerjoin(TaskRun, join)
                                   .where(*filters).group_by(bucket))).all())
    total = counts.get(status, 0) if status else sum(counts.values())
    if status:
        filters.append(bucket == status)
    rows = (await db.execute(select(ToolRun, TaskRun).outerjoin(TaskRun, join).where(*filters)
                            .order_by(ToolRun.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return ToolRunListOut(items=[_record_out(row, task) for row, task in rows], total=total,
                          page=page, page_size=page_size, counts={**counts, "all": sum(counts.values())})


@router.get("/runs/{run_id}", response_model=ToolRunRecordOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> ToolRunRecordOut:
    row = await get_tool_run(db, user.id, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    task = await sync_generation_record(db, row)
    await db.commit()
    return _record_out(row, task)
