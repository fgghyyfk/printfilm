"""电商批量任务业务层：校验上传、建任务、分页、重试与打包。"""
from __future__ import annotations

import asyncio
import io
import shutil
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomJob, EcomJobItem
from app.models_tasks import TaskRun
from app.schemas_tasks import TaskCreateRequest
from app.services import storage
from app.services.billing.estimates import estimate_task_fen
from app.services.billing.settlement import billing_active, ensure_balance_for_task_batch
from app.services.ecom_jobs import kinds
from app.services.ecom_jobs.jobs import ACTIVE_ITEMS, job_dir, public_url, reconcile_items, refresh_job_status

MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


# 在写文件、建任务前验证格式和尺寸，防止损坏图与超大解码占用资源。
def inspect_upload(data: bytes, filename: str) -> tuple[int, int]:
    if Path(filename).suffix.lower() not in ALLOWED_EXT:
        raise ValueError("商品图仅支持 png / jpg / webp")
    if not data:
        raise ValueError("请上传商品图")
    if len(data) > MAX_UPLOAD:
        raise ValueError("商品图不能超过 10MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > 40_000_000 or min(width, height) <= 0 or max(width, height) / min(width, height) > 4:
                raise ValueError("图片尺寸或比例不支持")
            orientation = image.getexif().get(274)
        with Image.open(io.BytesIO(data)) as verified:
            verified.verify()
        return (height, width) if orientation in {5, 6, 7, 8} else (width, height)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError("图片无法识别，请换一张") from exc


# 上传按真实解码内容统一保存 PNG，消除 EXIF 方向和伪造扩展名影响。
def save_upload(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as image:
        ImageOps.exif_transpose(image).convert("RGBA").save(dest, format="PNG")


# 创建单张价格探针，清晰度与实际任务 payload 保持一致。
async def image_unit_fen(db: AsyncSession, clarity: str) -> int:
    return int(await estimate_task_fen(db, TaskRun(
        domain="studio", task_type="ecom_job_image", payload={"clarity": clarity},
    )))


# 输出所有已实现 kind 的可选项和分档预估价。
async def get_options(db: AsyncSession, user: User) -> dict:
    options = {}
    for key, kind in kinds.KINDS.items():
        prices = {clarity: await image_unit_fen(db, clarity) for clarity in kind.clarities}
        options[key] = {"max_images": kind.max_images, "ratios": list(kind.ratios),
                        "clarities": list(kind.clarities), "prices": prices}
        if key == "style":
            options[key].update(mode_limits=kinds.STYLE_MODE_LIMITS, max_count=4)
        elif key == "buyer_show":
            options[key].update(max_count=8)
    return {"kinds": options, "billing_enabled": billing_active(user)}


# 给一组图片整批检查余额，再在同一事务中逐张创建 TaskRun。
async def enqueue_items(db: AsyncSession, user: User, job: EcomJob, items: list[EcomJobItem]) -> None:
    from app.services.tasks.service import create_task

    probe = TaskRun(domain="studio", task_type="ecom_job_image", requested_by=user.id,
                    payload={"clarity": job.params["clarity"]})
    await ensure_balance_for_task_batch(db, user, probe, len(items))
    for item in items:
        task = await create_task(db, user, TaskCreateRequest(
            domain="studio", task_type="ecom_job_image",
            payload={"job_id": job.id, "idx": item.idx, "kind": job.kind,
                     "clarity": job.params["clarity"], "size": item.size},
            dedupe_key=f"ecomjob:{job.id}:{item.idx}:{uuid.uuid4().hex[:8]}",
        ), commit=False)
        item.task_run_id, item.status, item.error = task.id, "queued", None


# 校验各角色数量后交给底座落盘；可选模特与场景仅限买家秀使用。
async def create_job(
    db: AsyncSession, user: User, *, kind: str, uploads: list[tuple[bytes, str]], params: dict, title: str = "",
    style_uploads: list[tuple[bytes, str]] | None = None,
    model_uploads: list[tuple[bytes, str]] | None = None,
    scene_uploads: list[tuple[bytes, str]] | None = None,
) -> EcomJob:
    clean = kinds.validate(kind, len(uploads), params)
    references = style_uploads or []
    if (kind == "style" and len(references) != 1) or (kind != "style" and references):
        raise ValueError("参数错误")
    if len(model_uploads or []) > 1 or len(scene_uploads or []) > 1:
        raise ValueError("参数错误")
    if kind != "buyer_show" and (model_uploads or scene_uploads):
        raise ValueError("参数错误")
    role_uploads = [("product", data, name) for data, name in uploads]
    for role, batch in (("style", references), ("model", model_uploads), ("scene", scene_uploads)):
        role_uploads.extend((role, data, name) for data, name in batch or [])
    combined = kind == "buyer_show" or (kind == "style" and clean["mode"] == "single")
    count = clean["count"] if combined else len(uploads)
    return await create_prepared_job(db, user, kind=kind, params=clean, title=title,
                                     uploads=role_uploads, count=count, combined=combined)


# 共用建任务流程：校验完整批次、独立保存引用、逐图提示词、原子入队及失败目录回收。
async def create_prepared_job(
    db: AsyncSession, user: User, *, kind: str, params: dict, title: str,
    uploads: list[tuple[str, bytes, str]], count: int, combined: bool,
) -> EcomJob:
    if len(title) > 128:
        raise ValueError("标题不能超过 128 字")
    sizes = [await asyncio.to_thread(inspect_upload, data, name) for _, data, name in uploads]
    await ensure_balance_for_task_batch(db, user, TaskRun(
        domain="studio", task_type="ecom_job_image", requested_by=user.id, payload={"clarity": params["clarity"]},
    ), count)
    job = EcomJob(user_id=user.id, kind=kind, title=title.strip(), params=params, status="queued")
    db.add(job)
    await db.flush()
    directory = job_dir(user.id, job.id)
    try:
        inputs = []
        for idx, (role, data, _) in enumerate(uploads):
            dest = directory / f"input_{idx}.png"
            await asyncio.to_thread(save_upload, data, dest)
            url = await asyncio.to_thread(public_url, dest)
            inputs.append({"role": role, "path": str(dest), "url": url})
        products = [ref for ref in inputs if ref["role"] == "product"]
        references = [ref for ref in inputs if ref["role"] != "product"]
        items = []
        for idx in range(count):
            item_inputs = inputs if combined else [products[idx]] + references
            ratio, size = kinds.image_geometry(params["ratio"], params["clarity"], sizes[0 if combined else idx])
            item = EcomJobItem(job_id=job.id, idx=idx, inputs=item_inputs,
                               prompt=kinds.build_item_prompt(kind, params, item_inputs, idx),
                               ratio=ratio, size=size, status="queued")
            db.add(item)
            items.append(item)
        await db.flush()
        await enqueue_items(db, user, job, items)
        await db.commit()
    except Exception:
        await db.rollback()
        await asyncio.to_thread(shutil.rmtree, directory, True)
        raise
    return await get_job(db, user, job.id)


# 按当前用户隔离读取 job，必要时同步被调度器终止的图片状态。
async def get_job(db: AsyncSession, user: User, job_id: int) -> EcomJob:
    job = (await db.execute(select(EcomJob).where(
        EcomJob.id == job_id, EcomJob.user_id == user.id,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if job is None:
        raise LookupError("任务不存在")
    if await reconcile_items(db, job):
        return await get_job(db, user, job_id)
    return job


# 服务端按用户和 kind 筛选分页，不把其它用户的任务带到客户端。
async def list_jobs(db: AsyncSession, user: User, *, page: int, page_size: int, kind: str | None) -> tuple[list[EcomJob], int]:
    if kind and kind not in kinds.KINDS:
        raise ValueError("未知工具")
    filters = [EcomJob.user_id == user.id]
    if kind:
        filters.append(EcomJob.kind == kind)
    total = int((await db.execute(select(func.count()).select_from(EcomJob).where(*filters))).scalar_one())
    rows = list((await db.execute(select(EcomJob).where(*filters).order_by(EcomJob.id.desc())
                                 .offset((page - 1) * page_size).limit(page_size))).scalars())
    for index, job in enumerate(rows):
        if await reconcile_items(db, job):
            rows[index] = await get_job(db, user, job.id)
    return rows, total


# 锁住父任务和图片，防止连续点击为同一失败图片重复入队扣费。
async def retry_item(db: AsyncSession, user: User, job_id: int, idx: int) -> EcomJob:
    job = (await db.execute(select(EcomJob).where(EcomJob.id == job_id, EcomJob.user_id == user.id)
                            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if job is None:
        raise LookupError("任务不存在")
    item = (await db.execute(select(EcomJobItem).where(EcomJobItem.job_id == job_id, EcomJobItem.idx == idx)
                             .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if item is None:
        raise LookupError("图片不存在")
    if item.status in ACTIVE_ITEMS:
        raise ValueError("这张图正在生成中")
    if item.status != "failed":
        raise ValueError("只能重试失败的图片")
    task = await db.get(TaskRun, item.task_run_id) if item.task_run_id else None
    if task and task.status not in {"failed", "cancelled"}:
        raise ValueError("原任务尚未结束，请稍后查看")
    await enqueue_items(db, user, job, [item])
    await refresh_job_status(db, job_id)
    await db.commit()
    return await get_job(db, user, job_id)


# 只打包已成功图片，优先读取本地副本，返回 ZIP 字节与安全文件名。
async def build_zip(db: AsyncSession, user: User, job_id: int) -> tuple[bytes, str]:
    job = await get_job(db, user, job_id)
    items = [item for item in job.items if item.status == "succeeded" and item.result_url]
    if not items:
        raise ValueError("还没有生成好的图片")
    files = []
    for item in items:
        local = storage.local_path_from_url(item.result_url)
        if local is None or not local.exists():
            # OSS URL 也可从本 job 目录找到生成时保留的本地副本。
            candidates = list(job_dir(user.id, job_id).glob(f"result_{item.idx}_*.png"))
            local = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
        if local is None:
            local = await storage.ensure_local_media(item.result_url, job_dir(user.id, job_id) / f"download_{item.idx}.png")
        files.append((local, f"{item.idx + 1:02d}.png"))
    data = await asyncio.to_thread(_zip_files, files)
    return data, f"ecom-{job.kind}-{job.id}.zip"


# 在工作线程压缩图片，避免大文件 ZIP 阻塞 API 事件循环。
def _zip_files(files: list[tuple[Path, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            archive.write(path, name)
    return buffer.getvalue()


# 序列化时隐藏本地路径，保留公开参考图角色及任务状态。
def to_out(job: EcomJob) -> dict:
    return {"id": job.id, "kind": job.kind, "title": job.title, "status": job.status, "params": job.params,
            "created_at": job.created_at, "updated_at": job.updated_at,
            "items": [{"idx": item.idx, "inputs": [{"role": ref["role"], "url": ref["url"]} for ref in item.inputs],
                       "prompt": item.prompt, "ratio": item.ratio, "size": item.size,
                       "result_url": item.result_url, "status": item.status, "error": item.error,
                       "task_run_id": item.task_run_id} for item in job.items]}
