from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Template
from app.schemas import TemplateDetailOut
from app.services.creation_templates import CREATION_TEMPLATE_IDS, normalize_creation_template

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get(
    "",
    response_model=list[TemplateDetailOut],
    summary="获取可用创作模板",
    description="无需登录，按预设顺序返回已启用且允许前台创作使用的动画模板，包含规范化后的模板详情。",
    response_description="可用创作模板列表",
)
async def list_templates(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(Template).where(Template.is_active.is_(True), Template.id.in_(CREATION_TEMPLATE_IDS))
    )
    by_id = {template.id: template for template in result.scalars().all()}
    return [
        normalize_creation_template(by_id[template_id])
        for template_id in CREATION_TEMPLATE_IDS
        if template_id in by_id
    ]


@router.get(
    "/{template_id}",
    response_model=TemplateDetailOut,
    summary="获取创作模板详情",
    description="无需登录，按模板标识读取已启用模板并返回规范化详情；不存在或已停用时返回 404。",
    response_description="模板详情",
)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    tpl = await db.get(Template, template_id)
    if not tpl or not tpl.is_active:
        raise HTTPException(status_code=404, detail="模板不存在")
    return normalize_creation_template(tpl)
