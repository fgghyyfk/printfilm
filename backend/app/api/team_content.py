"""团队项目列表、个人项目迁入与团队资产副本接口。"""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_drama import DramaAssetOut, DramaProjectCreate, DramaProjectOut
from app.services.teams import content

router = APIRouter(prefix="/teams/{team_id}", tags=["team-content"])
personal_router = APIRouter(prefix="/drama", tags=["drama-assets"])


class TeamProjectCreate(BaseModel):
    """真实项目创意由用户提供，不以占位文本冒充。"""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=512)
    source: str = Field(min_length=20, max_length=100000)
    episode_count: int = Field(default=1, strict=True, ge=1, le=120, description="初始集数，支持 1 至 120 集，后续可在项目中增加分集")
    workflow: Literal["script", "canvas"] = "script"


class TeamAssetCopy(BaseModel):
    """只接收目标项目，源内容和来源标记完全由数据库取得。"""
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, gt=0)


@router.get("/projects")
async def list_projects(team_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=200), status: Literal["draft", "producing", "completed"] | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """团队项目服务端搜索、状态筛选和分页。"""
    return await content.list_projects(db, team_id, user.id, page, page_size, q, status)


@router.get("/assets")
async def list_assets(team_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=200), type: Literal["character", "scene", "prop", "voice"] | None = None, project_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """仅返回当前团队可访问项目的角色、场景、道具和音色。"""
    return await content.list_assets(db, team_id, user.id, page, page_size, q, type, project_id)


@router.post("/projects", response_model=DramaProjectOut, status_code=201)
async def create_project(team_id: int, body: TeamProjectCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> DramaProjectOut:
    """复用已有漫剧创建流程，绑定团队但不自动开始收费生成。"""
    from app.api.drama.projects import create_project as create_drama_project
    return await create_drama_project(DramaProjectCreate(team_id=team_id, **body.model_dump()), db=db, user=user, scope_team_id=team_id)


@router.get("/transferable-projects")
async def transferable_projects(team_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """列出本人个人项目及当前不能迁入的具体原因。"""
    return await content.transferable_projects(db, team_id, user.id, page, page_size)


@router.post("/projects/{project_id}/transfer", response_model=DramaProjectOut)
async def transfer_project(team_id: int, project_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> DramaProjectOut:
    """迁入不改变原项目 ID 和任何历史计费账户。"""
    from app.api.drama.projects import get_project
    project, copier = await content.transfer_project(db, team_id, user.id, project_id)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if copier:
            copier.cleanup()
        raise
    return await get_project(project.id, db=db, user=user)


@router.post("/assets/{asset_id}/copy", response_model=DramaAssetOut, status_code=201)
async def copy_asset(team_id: int, asset_id: int, body: TeamAssetCopy, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> DramaAssetOut:
    """目标 asset:0 租约通过后创建独立副本，不下载任意公网地址。"""
    from fastapi import HTTPException
    try:
        version = int(request.headers.get("X-Edit-Version", "0"))
    except ValueError as exc:
        raise HTTPException(422, "无效的编辑版本") from exc
    asset, copier = await content.copy_asset(db, team_id, user.id, asset_id, body.project_id, request.headers.get("X-Edit-Session", ""), request.headers.get("X-Edit-Token", ""), version)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        copier.cleanup()
        raise
    return DramaAssetOut.model_validate(asset)


@personal_router.post("/assets/{asset_id}/copy", response_model=DramaAssetOut, status_code=201)
async def copy_owned_asset(asset_id: int, body: TeamAssetCopy, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> DramaAssetOut:
    """个人副本兼容入口；团队源目标仍必须同队并持目标编辑租约。"""
    from fastapi import HTTPException
    from app.models_drama import DramaProject

    target = await db.get(DramaProject, body.project_id)
    if not target:
        raise HTTPException(404, "目标项目不存在")
    return await copy_asset(target.team_id, asset_id, body, request, db=db, user=user)
