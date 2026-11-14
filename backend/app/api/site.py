"""站点公开配置：无需登录，供用户端启动时读取。"""
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.site_appearance import get_site_theme

router = APIRouter(prefix="/site", tags=["site"])


class SiteAppearanceOut(BaseModel):
    """站点外观。"""

    theme: str = Field(description="当前全站主题 ID")


@router.get(
    "/appearance",
    response_model=SiteAppearanceOut,
    summary="获取站点外观",
    description="无需登录，返回管理员设置的全站主题。",
    response_description="当前全站主题",
)
async def site_appearance(response: Response, db: AsyncSession = Depends(get_db)) -> SiteAppearanceOut:
    # 切换后希望尽快生效，只允许短时缓存
    response.headers["Cache-Control"] = "public, max-age=30"
    return SiteAppearanceOut(theme=await get_site_theme(db))
