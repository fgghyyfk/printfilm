"""Global read-only media catalog, separate from editable project asset IDs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_media_library import LibraryType, MediaLibraryPage
from app.services.drama.media_library import library_page
from app.services.teams.write_access import workspace_team

router = APIRouter()


@router.get("/library-assets", response_model=MediaLibraryPage)
async def list_library_assets(
    type: LibraryType = "all", project_id: int | None = Query(None, ge=1), unassigned: bool = False,
    q: str = Query("", max_length=200), page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
    team_id: int | None = Depends(workspace_team),
) -> MediaLibraryPage:
    return await library_page(db, user.id, team_id, kind=type, project_id=project_id,
                              unassigned=unassigned, q=q, page=page, page_size=page_size)
