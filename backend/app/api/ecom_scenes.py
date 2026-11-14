"""电商图场景库 API：/api/tools/ecom-scenes（只做鉴权、校验与调用 service）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services.ecom_set import scenes as svc

router = APIRouter(prefix="/tools/ecom-scenes", tags=["ecom-scenes"])


class EcomSceneOut(BaseModel):
    """一张场景卡。"""

    key: str
    name: str
    scope: str
    industries: list[str]
    description: str
    cover_url: str
    spec: dict
    group: str = ""
    festival: dict | None = None


class EcomSceneListOut(BaseModel):
    """场景分页列表。"""

    items: list[EcomSceneOut]
    total: int
    page: int
    page_size: int
    industries: list[dict]
    groups: list[dict] = []
    festival_types: list[dict] = []


# 场景列表：系统场景 + 我的场景，可按行业筛选（服务端分页）
@router.get("", response_model=EcomSceneListOut)
async def list_scenes(
    industry: str = Query("", max_length=16),
    group: str = Query("", max_length=16),
    festival_type: str = Query("", max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await svc.ensure_seeded()
    rows, total = await svc.list_scenes(db, user.id, industry=industry or None, group=group,
                                        festival_type=festival_type, page=page, page_size=page_size)
    groups, festival_types = svc.groups_out()
    return EcomSceneListOut(
        items=[EcomSceneOut(**svc.to_out(r)) for r in rows], total=total, page=page, page_size=page_size,
        industries=svc.industries_out(), groups=groups, festival_types=festival_types,
    )
