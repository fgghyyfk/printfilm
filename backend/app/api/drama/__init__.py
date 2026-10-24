"""Drama API package: /api/drama/*."""

from fastapi import APIRouter, Depends

from app.api.drama import agents, assets, canvas, episodes, generation, projects, scripts, skills
from app.services.teams.write_access import enforce_drama_write

router = APIRouter(prefix="/drama", tags=["drama"], dependencies=[Depends(enforce_drama_write)])
router.include_router(projects.router)
router.include_router(scripts.router)
router.include_router(agents.router)
router.include_router(assets.router)
router.include_router(episodes.router)
router.include_router(generation.router)
router.include_router(canvas.router)
router.include_router(skills.router)
