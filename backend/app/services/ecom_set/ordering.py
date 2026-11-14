"""套图调整顺序：改每张图的序号（影响结果页、详情长图与 zip 里的排列），不重新出图、不收费。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomSet


def _remap_layout(plan: dict, new_of: dict[int, int]) -> dict:
    """按新序号重排 styled.layout（每张的镜头与元素按序号存）。"""
    styled = plan.get("styled")
    if not isinstance(styled, dict) or not isinstance(styled.get("layout"), dict):
        return plan
    layout = {}
    for k, v in styled["layout"].items():
        try:
            layout[str(new_of.get(int(k), int(k)))] = v
        except (TypeError, ValueError):
            continue
    return {**plan, "styled": {**styled, "layout": layout}}


async def reorder_items(db: AsyncSession, user: User, set_id: int, order: list[int]) -> EcomSet:
    """order 为现有序号按新顺序排列（如 [0, 3, 1, 2]）。生成中不能调。"""
    from app.services.ecom_set.service import get_set

    ecom = await get_set(db, user, set_id)
    current = sorted(it.idx for it in ecom.items)
    if sorted(int(i) for i in order) != current:
        raise ValueError("顺序不完整，请刷新后重试")
    if any(it.status in {"queued", "running"} for it in ecom.items):
        raise ValueError("有图片正在生成，请生成完成后再调整顺序")
    new_of = {int(old): current[pos] for pos, old in enumerate(order)}
    if all(k == v for k, v in new_of.items()):
        return ecom
    items = list(ecom.items)
    # 序号有唯一约束：先挪到负数，再落到新位置
    for it in items:
        it.idx = -(it.idx + 1)
    await db.flush()
    for it in items:
        it.idx = new_of[-it.idx - 1]
    await db.flush()
    ecom.plan = _remap_layout(dict(ecom.plan or {}), new_of)
    await db.commit()
    db.expire(ecom)
    return await get_set(db, user, set_id)
