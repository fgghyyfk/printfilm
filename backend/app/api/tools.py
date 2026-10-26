"""Independent video, image and audio generation and task history."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from PIL import Image
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import ToolRun, User
from app.models_tasks import TaskRun
from app.models_drama import DramaAsset, DramaProject
from app.schemas_tools import ToolEstimateRequest, ToolReferenceAssetOut, ToolRunListOut, ToolRunOut, ToolRunRecordOut, ToolTaskOut
from app.services.billing import settle_deferred_video_poll
from app.services.billing.http import http_exception_for_value_error
from app.services.studio_generation import enqueue_generation, sync_generation_record, validate_generation
from app.services.studio_tools import get_tool_run, poll_image_tool_task, poll_video_task, save_upload, update_tool_run_task
from app.services.studio_references import (
    MAX_UPLOAD_BYTES, ROLE_KIND, asset_kind_expression, parse_references,
    personal_asset_filters, reference_params, validate_reference_mode, validate_upload,
)

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/config")
async def generation_config(user: User = Depends(get_current_user)) -> dict:
    from app.config import get_settings
    settings = get_settings()
    return {"models": {"video": settings.model_video, "image": settings.model_image,
                       "audio": settings.model_audio}}


@router.get("/reference-assets")
async def list_reference_assets(
    kind: Literal["image", "video", "audio"] | None = None,
    q: str = Query("", max_length=200), page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    media_kind = asset_kind_expression()
    filters = [*personal_asset_filters(user.id), media_kind.in_(["image", "video", "audio"])]
    if kind:
        filters.append(media_kind == kind)
    if q.strip():
        filters.append(DramaAsset.name.ilike(f"%{q.strip()}%"))
    query = select(DramaAsset, media_kind).join(DramaProject, DramaProject.id == DramaAsset.project_id).where(*filters)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.order_by(DramaAsset.updated_at.desc(), DramaAsset.id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [ToolReferenceAssetOut.model_validate({**asset.__dict__, "media_kind": media}) for asset, media in rows],
            "total": total or 0, "page": page, "page_size": page_size}


@router.post("/estimate")
async def estimate_generation(
    body: ToolEstimateRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    from app.services.billing.estimates import estimate_task_fen
    from app.services.billing.settlement import billing_active
    from app.services.media_models.planning import prepare_task_media
    params = body.model_dump(exclude={"tool_id", "prompt"})
    params["references"] = parse_references(params["references"])
    try:
        validate_reference_mode(body.video_mode, params["references"])
        params.update(await reference_params(db, user, params["references"]))
        validate_generation(body.tool_id, body.prompt, params, 0, quote=True)
        payload = {**params, "tool_id": body.tool_id, "duration": int(str(body.duration).removesuffix("s")),
                   "generate_audio": False}
        task = TaskRun(domain="studio", task_type="tool_video", requested_by=user.id, payload=payload)
        await prepare_task_media(db, task)
        enabled = billing_active(user)
        estimate = await estimate_task_fen(db, task) if enabled else 0
    except ValueError as exc:
        raise http_exception_for_value_error(exc) from exc
    snapshot = task.billing_snapshot or {}
    return {"estimate_fen": estimate, "estimate_yuan": round(estimate / 100, 2), "billing_enabled": enabled,
            "billing_mode": snapshot.get("mode", "legacy"), "price_version": snapshot.get("price_version"),
            "items": [{key: value for key, value in item.items() if key != "route"} for item in snapshot.get("items", [])]}


@router.post("/run", response_model=ToolRunOut)
async def run_tool(
    tool_id: str = Form(..., max_length=16), prompt: str = Form("", max_length=7000),
    negative: str = Form("", max_length=2000), ratio: str = Form("", max_length=16),
    strength: str = Form("", max_length=16), mode: str = Form("", max_length=32),
    pack: str = Form("", max_length=32), duration: str = Form("", max_length=16),
    motion: str = Form("", max_length=16), voice: str = Form("narrator_calm", max_length=64),
    model_id: str = Form("", max_length=128), resolution: str = Form("", max_length=16),
    video_mode: str = Form("", max_length=32), references: str = Form("[]", max_length=8000),
    price_version: int | None = Form(default=None, ge=0),
    files: list[UploadFile] | None = File(default=None),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> ToolRunOut:
    tid = tool_id.strip()
    params = dict(negative=negative, ratio=ratio, strength=strength, mode=mode, pack=pack,
                  duration=duration, motion=motion, voice=voice, model_id=model_id, resolution=resolution)
    saved = []
    try:
        if video_mode:
            if tid not in {"t2v", "v2v"}:
                raise ValueError("video_mode: 仅视频生成可使用此模式")
            refs = parse_references(references)
            validate_reference_mode(video_mode, refs, len(files or []))
            params.update(video_mode=video_mode, references=refs)
            params.update(await reference_params(db, user, refs))
        elif references != "[]":
            raise ValueError("references: 请先选择视频生成模式")
        if price_version:
            params["price_version"] = price_version
        validate_generation(tid, prompt, params, len(files or []))
        upload_kinds = {ref["file_index"]: ROLE_KIND[ref["role"]] for ref in params.get("references", []) if "file_index" in ref}
        for index, item in enumerate(files or []):
            raw = await item.read(MAX_UPLOAD_BYTES + 1)
            if not raw or len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError("文件不能为空，且单个文件不能超过 40MB")
            name = item.filename or "upload.png"
            suffix = name.rsplit(".", 1)[-1].lower()
            if video_mode:
                validate_upload(raw, name, upload_kinds[index], item.content_type)
            elif suffix in {"mp4", "mov", "webm"} and tid == "v2v":
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
        if video_mode:
            params.update(await reference_params(db, user, params["references"], files=saved))
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
    private = {"file_paths"}
    if (row.params or {}).get("video_mode"):
        private.update({"first_frame", "last_frame", "reference_images", "reference_videos", "reference_audios"})
    params = {key: value for key, value in (row.params or {}).items() if key not in private}
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
