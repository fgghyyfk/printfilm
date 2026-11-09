"""电商图元素库 API：/api/tools/ecom-elements/*（只做鉴权、校验与调用 service）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services.ecom_set import elements as svc

router = APIRouter(prefix="/tools/ecom-elements", tags=["ecom-elements"])
logger = logging.getLogger(__name__)


async def _guard(db: AsyncSession, coro):
    """service 异常转中文 HTTP 错误。"""
    try:
        return await coro
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", summary="元素列表（系统预置 + 我的元素，服务端分页）")
async def list_elements(
    category: str = Query("", max_length=16), q: str = Query("", max_length=20), industry: str = Query("", max_length=16),
    mine: bool = Query(False), page: int = Query(1, ge=1), page_size: int = Query(60, ge=1, le=200),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    rows, total = await svc.list_elements(db, user, category=category, q=q, industry=industry, mine=mine,
                                          page=page, page_size=page_size)
    return {"items": [svc.to_out(r) for r in rows], "total": total, "page": page, "page_size": page_size,
            "categories": svc.categories_out()}


@router.get("/recommend", summary="按商品资料与行业推荐元素")
async def recommend(product_id: int | None = Query(None), industry: str = Query("", max_length=16),
                    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    knowledge = None
    if product_id:
        from app.services.ecom_set import products

        row = await _guard(db, products.get_product(db, user, product_id))
        knowledge = row.knowledge
    rows = await svc.recommend(db, user, knowledge=knowledge, industry=industry)
    return {"items": [svc.to_out(r) for r in rows]}


@router.post("", summary="新建我的元素（一句话描述，可附实物照片）")
async def create_element(
    name: str = Form(..., max_length=32), category: str = Form("vessel", max_length=16),
    prompt: str = Form("", max_length=200), file: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    image = (await file.read(), file.filename or "element.png") if file is not None else None
    return svc.to_out(await _guard(db, svc.create_element(db, user, name=name, category=category, prompt=prompt,
                                                          image=image)))


@router.delete("/{key}", summary="删除我的元素")
async def delete_element(key: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await _guard(db, svc.delete_element(db, user, key))
    return {"ok": True}
