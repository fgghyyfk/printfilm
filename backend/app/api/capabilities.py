"""Public feature availability without authentication or database access."""

from fastapi import APIRouter

from app.config import get_settings


router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("")
async def capabilities() -> dict[str, bool]:
    return {"drama_voice_generation_enabled": get_settings().drama_voice_generation_enabled}
