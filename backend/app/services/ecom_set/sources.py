"""只从当前用户已成功的精修结果中读取套图商品图。"""
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import storage
from app.services.ecom_jobs.service import get_job
from app.services.ecom_jobs.jobs import job_dir


# 校验所属用户、工具类型和成功状态；优先复用本地成图，不接受客户端任意 URL。
async def refine_product(db: AsyncSession, user: User, job_id: int, idx: int) -> Path:
    job = await get_job(db, user, job_id)
    if job.kind != "refine":
        raise ValueError("请选择精修结果")
    item = next((item for item in job.items if item.idx == idx), None)
    if item is None or item.status != "succeeded" or not item.result_url:
        raise ValueError("精修图片尚未完成")
    local = storage.local_path_from_url(item.result_url)
    if local is not None and local.is_file():
        return local
    directory = job_dir(user.id, job.id)
    candidates = list(directory.glob(f"result_{idx}_*.png"))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return await storage.ensure_local_media(item.result_url, directory / f"set_source_{idx}.png")
