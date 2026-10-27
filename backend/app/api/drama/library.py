"""Global read-only media catalog, separate from editable project asset IDs."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_media_library import LibraryGenerationStatus, LibraryProjectKind, LibraryType, MediaKind, MediaLibraryPage
from app.services.drama.media_library import library_page
from app.services.teams.write_access import workspace_team

router = APIRouter()


@router.get(
    "/library-assets", response_model=MediaLibraryPage,
    summary="分页查询只读数字资产库",
    description="汇总当前个人或团队空间的项目资产、分镜视频和生成结果，筛选后按更新时间倒序分页。使用 team_id 查询参数或 X-Team-Id 请求头选择团队空间；未指定时查询本人空间。返回的是只读目录，key 及 source_kind 用于区分不同来源的编号，不能将目录中的任意 id 直接作为可编辑项目资产编号。超出范围的页码回落到最后一页。",
    response_description="数字资产目录分页结果，meta.total 为包含生成状态在内的全部筛选后的数量，meta.total_all 为空间及归属范围内筛选前数量",
)
async def list_library_assets(
    type: Annotated[LibraryType, Query(description="素材类别：all 查询全部；character、scene、prop、voice 按资产类型筛选；image、video、audio 按媒体种类筛选")] = "all",
    media_kind: Annotated[MediaKind | None, Query(description="媒体种类，可选 image、video、audio；可与 type 叠加筛选")] = None,
    generation_status: Annotated[LibraryGenerationStatus, Query(description="生成状态：all 为全部；generated 为当前媒体已有正式结果（含上传）；ungenerated 为当前媒体尚无正式结果。图片按可用 url 或 cover 判断，视频和音频必须有对应类型的媒体 url，封面不算视频成片；与其他条件叠加后再统计、分页")] = "all",
    project_id: int | None = Query(None, ge=1, description="所属项目编号，按 project_kind 区分编号空间；指定不可访问项目时返回 404"),
    unassigned: Annotated[bool, Query(description="仅查询未归属项目的生成结果；通常不与 project_id 同时使用")] = False,
    project_kind: Annotated[LibraryProjectKind, Query(description="project_id 对应的项目种类：drama 为漫剧或画布，short_video 为短视频；未指定 project_id 时不单独按此字段限制全部目录")] = "drama",
    q: str = Query("", max_length=200, description="在素材名、项目标题、素材类型及项目编号中做大小写不敏感的子串搜索，忽略首尾空白"),
    page: int = Query(1, ge=1, description="页码，从 1 开始，超出范围时返回最后一页"),
    page_size: int = Query(12, ge=1, le=50, description="每页素材数，范围为 1 至 50"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
    team_id: int | None = Depends(workspace_team),
) -> MediaLibraryPage:
    return await library_page(db, user.id, team_id, kind=type, media_filter=media_kind,
                              generation_status=generation_status, project_id=project_id,
                              project_kind=project_kind, unassigned=unassigned, q=q, page=page, page_size=page_size)
