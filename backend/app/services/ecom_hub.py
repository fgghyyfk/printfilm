"""电商工作台最近任务：数据库合并两类历史后统一筛选、排序与分页。"""
from sqlalchemy import func, literal, select, union_all

from app.models_ecom import EcomJob, EcomSet
from app.services.ecom_jobs.kinds import KINDS


# 只查询当前用户的轻量摘要；以类型和 ID 打破相同时间的排序平局。
async def list_recent(db, user, *, page: int, page_size: int, kind: str | None) -> dict:
    if kind and kind not in {"set", *KINDS}:
        raise ValueError("未知工具")
    sets = select(EcomSet.id, literal("set").label("kind"), EcomSet.name.label("title"),
                  EcomSet.status, EcomSet.created_at).where(EcomSet.user_id == user.id)
    jobs = select(EcomJob.id, EcomJob.kind, EcomJob.title, EcomJob.status,
                  EcomJob.created_at).where(EcomJob.user_id == user.id)
    history = union_all(sets, jobs).subquery()
    query = select(history)
    if kind:
        query = query.where(history.c.kind == kind)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())))
    rows = (await db.execute(query.order_by(history.c.created_at.desc(), history.c.kind, history.c.id.desc())
                             .offset((page - 1) * page_size).limit(page_size))).mappings().all()
    return {"items": [dict(row) for row in rows], "meta": {"page": page, "page_size": page_size, "total": total}}
