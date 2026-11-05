"""复用电商成功图作为套图商品图，保留 E2 入口名称以兼容已有调用。"""
from pathlib import Path

from app.models import User
from app.services.ecom_jobs.sources import result_path
from sqlalchemy.ext.asyncio import AsyncSession


# 由共用来源校验读取当前用户的任意电商成功结果，不接受任意 URL。
async def refine_product(db: AsyncSession, user: User, job_id: int, idx: int) -> Path:
    return await result_path(db, user, job_id, idx)
