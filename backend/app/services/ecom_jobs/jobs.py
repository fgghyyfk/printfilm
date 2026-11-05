"""批量图片 TaskRun handler：生成、落本地、透明处理、计费及状态聚合。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_ecom import EcomJob, EcomJobItem
from app.models_tasks import TaskRun
from app.services import storage
from app.services.ecom_jobs.mock import generate_mock
from app.services.ecom_jobs.style import generate_mock as generate_style_mock
from app.services.ecom_jobs.refine import NEGATIVE_PROMPT, remove_white_background
from app.services.public_errors import public_message

logger = logging.getLogger(__name__)
ACTIVE_ITEMS = {"queued", "running"}


# 电商任务目录统一挂在 p0 下，按用户和 job 隔离。
def job_dir(user_id: int, job_id: int) -> Path:
    path = storage.project_dir(0) / "ecom" / f"u{user_id}" / "jobs" / f"j{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# mock 只返回本地 URL；真实出图沿用现有存储发布规则。
def public_url(path: Path) -> str:
    if get_settings().ark_mock:
        return storage.rel_static_url(path)
    return storage.publish_local(path, sync=True)


# 锁住父任务后汇总各张状态，避免并发完成覆盖最终状态。
async def refresh_job_status(db: AsyncSession, job_id: int) -> str:
    job = (await db.execute(select(EcomJob).where(EcomJob.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        return ""
    statuses = list((await db.execute(select(EcomJobItem.status).where(EcomJobItem.job_id == job_id))).scalars())
    if any(status == "running" for status in statuses):
        job.status = "running"
    elif any(status == "queued" for status in statuses):
        job.status = "queued"
    elif statuses and all(status == "succeeded" for status in statuses):
        job.status = "done"
    elif "succeeded" in statuses:
        job.status = "partial"
    else:
        job.status = "failed"
    await db.flush()
    return job.status


# 读取时把调度器已终止的任务同步到图片状态，避免页面永久轮询。
async def reconcile_items(db: AsyncSession, job: EcomJob) -> bool:
    active = {item.task_run_id: item for item in job.items if item.status in ACTIVE_ITEMS and item.task_run_id}
    if not active:
        return False
    rows = (await db.execute(select(TaskRun.id, TaskRun.status).where(TaskRun.id.in_(active)))).all()
    ended = [(task_id, status) for task_id, status in rows if status in {"failed", "cancelled"}]
    if not ended:
        return False
    await db.execute(select(EcomJob.id).where(EcomJob.id == job.id).with_for_update())
    for task_id, status in ended:
        # 条件更新避免读取后的旧任务结果覆盖并发的成功结果或新重试。
        from sqlalchemy import update
        await db.execute(update(EcomJobItem).where(
            EcomJobItem.id == active[task_id].id, EcomJobItem.task_run_id == task_id,
            EcomJobItem.status.in_(ACTIVE_ITEMS),
        ).values(status="failed", error="任务已取消" if status == "cancelled" else "生成失败，请重试"))
    await refresh_job_status(db, job.id)
    await db.commit()
    return True


# 统一将模型输出转成 PNG；透明底仅移除四边连通近白像素。
def finalize_image(source: Path, dest: Path, transparent: bool) -> None:
    with Image.open(source) as raw:
        result = remove_white_background(raw) if transparent else raw.convert("RGB")
        result.save(dest, format="PNG")


# 生图结果先落本地，mock 路径完全绕过模型和上传。
async def generate_image(db: AsyncSession, job: EcomJob, item: EcomJobItem) -> tuple[Path, Any]:
    params = job.params or {}
    directory = job_dir(job.user_id, job.id)
    source = directory / f"base_{item.idx}_{uuid.uuid4().hex[:8]}.png"
    if get_settings().ark_mock:
        if job.kind == "style":
            reference = next(Path(ref["path"]) for ref in item.inputs if ref["role"] == "style")
            products = [Path(ref["path"]) for ref in item.inputs if ref["role"] == "product"]
            await asyncio.to_thread(generate_style_mock, reference, products, source, size=item.size)
        else:
            await asyncio.to_thread(generate_mock, Path(item.inputs[0]["path"]), source,
                                    size=item.size, background=params.get("background", "white"))
        return source, None
    from app.services.ark import get_ark
    # 传公开参考地址，结构化通路由网关按需转换本地图片，文本降级不会夹带 base64。
    refs = [storage.to_public_url(ref["url"]) for ref in item.inputs if ref["role"] == "product"]
    style_refs = [storage.to_public_url(ref["url"]) for ref in item.inputs if ref["role"] == "style"]
    result = await get_ark().gen_image(
        item.prompt, NEGATIVE_PROMPT, refs, project_id=0, shot_no=job.user_id,
        size=item.size, aspect_ratio=item.ratio, structured_refs=True,
        **({"style_ref_urls": style_refs} if style_refs else {}),
    )
    url = result.local_url or result.remote_url
    if not url:
        raise RuntimeError("生图未返回图片")
    return await storage.ensure_local_media(url, source), result


# 单张任务失败时回写可读短句并重抛，由统一任务平台退回冻结费用。
async def run_item_job(task: TaskRun) -> dict[str, Any]:
    from app.database import AsyncSessionLocal
    from app.services.drama.billing_util import record_seedream_image_usage

    payload = task.payload or {}
    job_id, idx = int(payload.get("job_id") or 0), int(payload.get("idx", -1))
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(EcomJob).where(EcomJob.id == job_id).with_for_update())).scalar_one_or_none()
        item = (await db.execute(select(EcomJobItem).where(
            EcomJobItem.job_id == job_id, EcomJobItem.idx == idx,
        ))).scalar_one_or_none()
        if job is None or item is None or job.user_id != task.requested_by:
            raise ValueError("任务或图片不存在")
        if item.task_run_id != task.id or item.status == "succeeded":
            return {"ok": True, "skipped": "superseded"}
        item_id = item.id
        item.status, item.error, job.status = "running", None, "running"
        await db.commit()
        try:
            base, result = await generate_image(db, job, item)
            dest = job_dir(job.user_id, job_id) / f"result_{idx}_{uuid.uuid4().hex[:8]}.png"
            await asyncio.to_thread(finalize_image, base, dest, job.params.get("background") == "transparent")
            url = await asyncio.to_thread(public_url, dest)
            await db.execute(select(EcomJob.id).where(EcomJob.id == job_id).with_for_update())
            if result is not None:
                await record_seedream_image_usage(
                    db, user_id=job.user_id, model=get_settings().model_image,
                    domain="studio", image_result=result,
                    extra_raw={"tool_id": f"ecom_{job.kind}", "job_id": job_id, "idx": idx},
                )
            item.result_url, item.status, item.error = url, "succeeded", None
            await refresh_job_status(db, job_id)
            await db.commit()
            return {"ok": True, "job_id": job_id, "idx": idx, "url": url}
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            await db.execute(select(EcomJob.id).where(EcomJob.id == job_id).with_for_update())
            failed_item = await db.get(EcomJobItem, item_id)
            if failed_item and failed_item.task_run_id == task.id:
                failed_item.status, failed_item.error = "failed", public_message(exc)
            await refresh_job_status(db, job_id)
            await db.commit()
            logger.exception("电商出图失败 job=%s idx=%s", job_id, idx)
            raise
