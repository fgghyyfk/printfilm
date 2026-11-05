"""电商工作台的聚合历史与批量结果转带货接口。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ecom_jobs import EcomJobRoute, _guard
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_ecom import EcomToProjectIn, EcomToProjectOut
from app.schemas_ecom_hub import EcomJobToProjectIn, EcomRecentOut, EcomVideoLink
from app.services.daihuo import job_source
from app.services.ecom_hub import list_recent

router = APIRouter(prefix="/tools/ecom", tags=["ecom-hub"], route_class=EcomJobRoute)


# 两类历史在数据库按时间合并后分页；筛选类型包括套图及五类批量工具。
@router.get("/recent", response_model=EcomRecentOut)
async def recent(page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=50), kind: str | None = None,
                 db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _guard(db, list_recent(db, user, page=page, page_size=page_size, kind=kind))


# 检查来源归属后提供最近的带货项目，避免刷新后误建另一条视频。
@router.get("/jobs/{job_id}/project", response_model=EcomVideoLink)
async def linked_project(job_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _guard(db, job_source.owned_job(db, user, job_id))
    project = await _guard(db, job_source.latest_project(db, user, job_id))
    return {"project_id": project.id if project else None}


# 用当前用户至少两张成功图建草稿，后续写稿沿用现有阶段任务与计费。
@router.post("/jobs/{job_id}/project", response_model=EcomToProjectOut)
async def create_project(job_id: int, body: EcomJobToProjectIn,
                         db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    project = await _guard(db, job_source.create_project_from_job(db, user, job_id, **body.model_dump()))
    return {"project_id": project.id}


# 只读报价，不创建任何项目、任务或余额冻结。
@router.post("/jobs/{job_id}/project/quote")
async def quote_project(job_id: int, body: EcomToProjectIn,
                        db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _guard(db, job_source.quote_project_from_job(db, user, job_id,
                         output_ratio=body.output_ratio, voice_id=body.voice_id))


# 来源面板只授权写稿，即使旧页面重试，也不自动进入配音等下一收费阶段。
@router.post("/projects/{project_id}/script", response_model=EcomToProjectOut)
async def start_script(project_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.daihuo.images import start_image_project_script

    project = await _guard(db, start_image_project_script(db, user, project_id))
    return {"project_id": project.id}
