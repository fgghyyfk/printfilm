"""Independent video, image and audio generation and task history."""
from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from typing import Literal

from pydantic import BaseModel, Field
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
from app.services.public_errors import public_http_exception
from app.services.billing.settlement import finish_prepared_task_settlements
from app.services.studio_generation import enqueue_generation, sync_generation_record, validate_generation
from app.services.studio_tools import get_tool_run, poll_image_tool_task, poll_video_task, save_upload, update_tool_run_task
from app.services import tool_models
from app.services.studio_references import (
    IMAGE_ASSET_TOOLS, MAX_UPLOAD_BYTES, ROLE_KIND, asset_kind_expression, materialize_image_assets,
    parse_image_asset_references, parse_references, personal_asset_filters, reference_params,
    validate_reference_mode, validate_upload,
)

router = APIRouter(prefix="/tools", tags=["tools"])
logger = logging.getLogger(__name__)


@router.get(
    "/config",
    summary="获取独立生成工具模型配置",
    description="需要登录，返回视频、图像和音频工具当前配置的默认模型。",
    response_description="工具默认模型配置",
)
async def generation_config(user: User = Depends(get_current_user)) -> dict:
    from app.config import get_settings
    settings = get_settings()
    return {"models": {"video": settings.model_video, "image": settings.model_image,
                       "audio": settings.model_audio}}


@router.get(
    "/reference-assets",
    summary="分页查询个人参考资产",
    description="需要登录，分页读取本人个人空间可用的图片、视频和音频参考资产，支持媒体类型与名称关键词筛选。",
    response_description="参考资产及分页信息",
)
async def list_reference_assets(
    kind: Literal["image", "video", "audio"] | None = None,
    q: str = Query("", max_length=200, description="参考资产名称搜索关键词"), page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(24, ge=1, le=50, description="每页返回的记录数"),
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


def _model_out(asset: DramaAsset) -> dict:
    """模特库条目：资产字段 + 是否默认。"""
    return {**ToolReferenceAssetOut.model_validate({**asset.__dict__, "media_kind": "image"}).model_dump(),
            "is_default": tool_models.is_default_model(asset)}


@router.get(
    "/models",
    summary="获取模特库",
    description="需要登录，返回本人加入模特库的图片资产，默认模特排第一（is_default=true）。图片生成时可选其一作为固定模特。",
    response_description="模特列表",
)
async def list_tool_models(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return {"items": [_model_out(a) for a in await tool_models.list_models(db, user.id)]}


class ToolModelUpdate(BaseModel):
    is_model: bool = Field(True, description="true 加入模特库，false 移出")
    is_default: bool = Field(False, description="设为默认模特（会取消其他默认）")


@router.put(
    "/models/{asset_id}",
    summary="加入/移出模特库或设为默认",
    description="需要登录，只能操作本人个人空间的图片资产。is_default=true 时该模特成为图片生成页的默认模特。",
    response_description="更新后的模特库",
)
async def update_tool_model(asset_id: int, body: ToolModelUpdate, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)) -> dict:
    try:
        await tool_models.update_model(db, user.id, asset_id, is_model_flag=body.is_model,
                                       is_default=body.is_model and body.is_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return {"items": [_model_out(a) for a in await tool_models.list_models(db, user.id)]}


class ToolModelGenerate(BaseModel):
    description: str = Field(..., min_length=4, max_length=200, description="一句话描述模特：年龄、长相、发型、气质")
    name: str = Field("", max_length=60, description="模特名称（可选）")
    make_default: bool = Field(False, description="生成后设为默认模特")


@router.post(
    "/models/generate",
    summary="一句话生成新模特",
    description="需要登录，按描述生成一张原创模特定妆照（收一张生图费），存进「模特库」并加入模特库，返回更新后的模特库。",
    response_description="模特列表",
)
async def generate_tool_model(body: ToolModelGenerate, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)) -> dict:
    try:
        await tool_models.generate_model(db, user, description=body.description, name=body.name,
                                         make_default=body.make_default)
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc
    return {"items": [_model_out(a) for a in await tool_models.list_models(db, user.id)]}


@router.post(
    "/models/upload",
    summary="上传模特照片",
    description="需要登录，上传一张模特照片（PNG/JPG/WebP，≤10MB），存进「模特库」并加入模特库，可设为默认；不收费。",
    response_description="模特列表",
)
async def upload_tool_model(file: UploadFile = File(...), name: str = Form(""), make_default: bool = Form(False),
                            db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    try:
        await tool_models.upload_model(db, user, data=await file.read(), filename=file.filename or "model.jpg",
                                       name=name, make_default=make_default)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [_model_out(a) for a in await tool_models.list_models(db, user.id)]}


@router.post(
    "/estimate",
    summary="估算独立视频生成费用",
    description="需要登录，校验视频生成参数和参考素材，按当前计费配置返回估算费用、计费模式及价格版本；不会提交生成任务。金额提供分和元两种单位。",
    response_description="生成费用估算与价格信息",
)
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


@router.post(
    "/run",
    response_model=ToolRunOut,
    summary="提交独立媒体生成任务",
    description=(
        "需要登录，通过 multipart/form-data 提交图片、视频或音频生成参数及参考文件，校验通过后加入后台队列。"
        "单个参考文件最大 40 MB；参考素材使用 JSON references 配置。"
        "返回当前记录，生成结果通过任务查询获取。"
    ),
    response_description="已提交的工具生成记录",
)
async def run_tool(
    tool_id: str = Form(..., max_length=16, description="工具标识：t2i 文生图、i2i 图生图、i2p 商品图、ecom 电商图片、t2v 文生视频、v2v 参考视频、audio 配音"), prompt: str = Form("", max_length=7000, description="生成提示词或音频朗读文本"),
    negative: str = Form("", max_length=2000, description="反向提示词，用于描述需要避免的内容"), ratio: str = Form("", max_length=16, description="输出画面比例，按所选模型支持的值填写"),
    strength: str = Form("", max_length=16, description="图生图参考相似度：低允许较大变化，高尽量保留参考图；其他值使用中等强度"), mode: str = Form("", max_length=32, description="商品图模式，如白底图、详情长图或场景图"),
    pack: str = Form("", max_length=32, description="电商图片组合方式：主图拼接、详情排版或卖点海报"), duration: str = Form("", max_length=16, description="生成视频的时长，如 5s；支持范围取决于所选模型"),
    motion: str = Form("", max_length=16, description="视频运动幅度：弱、强，其他值使用中等运动"), voice: str = Form("narrator_calm", max_length=64, description="配音音色：narrator_calm、warm_storyteller 或 teacher_clear"),
    model_id: str = Form("", max_length=128, description="生成模型标识；留空时使用工具默认模型"), resolution: str = Form("", max_length=16, description="输出分辨率，按所选模型支持的值填写"),
    video_mode: str = Form("", max_length=32, description="视频模式：text_to_video 文生视频、first_last_frame 首尾帧、omni_reference 全能参考；留空兼容旧流程"), references: str = Form("[]", max_length=8000, description="最多 12 项的 JSON 数组；每项包含 role，并且仅提供 file_index 上传文件索引或 asset_id 个人资产编号之一"),
    price_version: int | None = Form(default=None, ge=0, description="客户端确认的价格版本；传入有效版本时用于价格一致性校验"),
    model_asset_id: int | None = Form(default=None, ge=1, description="固定模特的资产编号（须已加入模特库，且是 references 里第 1 张）；会在提示词前加身份一致约束"),
    color_ref: int | None = Form(default=None, ge=0, le=8, description="图生图自动校色的色板参考图序号（资产库所选在前、上传文件在后，从 0 开始）；留空不校色"),
    files: list[UploadFile] | None = File(default=None, description="参考图片、视频或音频文件；单个文件最大 40 MB，格式由工具及素材角色决定"),
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
            if tid not in IMAGE_ASSET_TOOLS:
                raise ValueError("references: 请先选择视频生成模式")
            # 图生图 / 图生产品：资产库里的图片作为参考，先复制成本次参考文件
            asset_ids = parse_image_asset_references(references)
            if model_asset_id:
                await tool_models.get_model(db, user.id, model_asset_id)
                tool_models.check_model_first(asset_ids, model_asset_id)
                params["model_asset_id"] = model_asset_id
            if len(asset_ids) + len(files or []) > 9:
                raise ValueError("references: 参考图合计最多 9 张")
            saved.extend(await materialize_image_assets(db, user, asset_ids))
            params["reference_asset_ids"] = asset_ids
        if model_asset_id and "model_asset_id" not in params:
            raise ValueError("model_asset_id: 选择模特后需从资产库带上模特图")
        if price_version:
            params["price_version"] = price_version
        if color_ref is not None:
            if tid not in IMAGE_ASSET_TOOLS:
                raise ValueError("color_ref: 只有图生图可以自动校色")
            if color_ref >= len(saved) + len(files or []):
                raise ValueError("color_ref: 色板参考图不存在")
            params["color_match_ref"] = color_ref
        validate_generation(tid, prompt, params, len(files or []) + len(saved))
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
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        for path in saved:
            path.unlink(missing_ok=True)
        # 本站明确的 HTTP 异常（如登录失效 401）原样返回；其余只回安全文案与稳定错误码，原文进日志
        if isinstance(exc, HTTPException):
            raise
        logger.exception("tool request failed tool=%s", tid)
        raise public_http_exception(exc) from exc
    return ToolRunOut.model_validate(data)


@router.get(
    "/tasks/{task_id}",
    response_model=ToolTaskOut,
    summary="查询独立生成任务状态",
    description="需要登录，仅可查询本人的工具任务。返回当前状态、媒体地址和错误信息；兼容旧任务时可能查询供应商并同步结算，新任务由服务端后台轮询。",
    response_description="工具任务当前状态与结果",
)
async def get_tool_task(task_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> ToolTaskOut:
    row = (await db.execute(select(ToolRun).where(ToolRun.user_id == user.id, ToolRun.task_id == task_id).limit(1))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    billing_task_id = None
    raw_bid = row.params.get("billing_task_id") if isinstance(row.params, dict) else None
    if raw_bid is not None:
        try:
            billing_task_id = int(raw_bid)
        except (TypeError, ValueError):
            billing_task_id = None
    prepared = None
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
        # 兼容迁移前的上游任务编号；新任务由服务端后台轮询。
        data = await poll_video_task(user, task_id)
        prepared = await settle_deferred_video_poll(
            db,
            user,
            provider_task_id=task_id,
            poll_status=str(data.get("status") or ""),
            result_data=data,
            error=str(data.get("error") or "") or None,
            billing_task_id=billing_task_id,
            usage_tokens=int((data.get("usage") or {}).get("total_tokens") or 0),
            completion_tokens=int((data.get("usage") or {}).get("completion_tokens") or 0),
            raw_usage=data.get("raw_usage") if isinstance(data.get("raw_usage"), dict) else None,
            upstream_cost_fen=data.get("upstream_cost_fen"),
        )
        await update_tool_run_task(db, user.id, task_id, data, billing_task_id=billing_task_id)
    await db.commit()
    if prepared:
        await finish_prepared_task_settlements(db, prepared)
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


@router.get(
    "/runs",
    response_model=ToolRunListOut,
    summary="分页查询独立生成历史",
    description="需要登录，分页返回本人的工具生成历史，支持媒体类型、状态、任务标识和创建时间筛选，并返回各状态数量统计。",
    response_description="工具生成记录、分页与状态统计",
)
async def list_runs(
    page: int = Query(1, ge=1, description="页码，从 1 开始"), page_size: int = Query(8, ge=1, le=50, description="每页返回的记录数"),
    kind: str | None = Query(None, pattern="^(video|image|audio)$", description="媒体类型：video 视频、image 图片、audio 音频"),
    status: str | None = Query(None, pattern="^(processing|succeeded|failed|cancelled)$", description="记录状态：processing 处理中、succeeded 成功、failed 失败、cancelled 已取消"),
    task_id: str | None = Query(None, max_length=128, description="任务标识，可匹配工具、平台或供应商任务编号"),
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


@router.get(
    "/runs/{run_id}",
    response_model=ToolRunRecordOut,
    summary="获取独立生成记录详情",
    description="需要登录，仅可读取本人的工具运行记录，先同步关联平台任务状态后返回结果；记录不存在返回 404。",
    response_description="工具生成记录详情",
)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> ToolRunRecordOut:
    row = await get_tool_run(db, user.id, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    task = await sync_generation_record(db, row)
    await db.commit()
    return _record_out(row, task)
