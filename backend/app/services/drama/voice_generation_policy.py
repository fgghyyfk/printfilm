"""Admission switch for new character voice design; running work is unaffected."""

from fastapi import HTTPException

from app.config import get_settings


VOICE_GENERATION_TASK_TYPES = frozenset({"voice_prompt", "voice_synthesis"})


def ensure_voice_generation_enabled() -> None:
    if not get_settings().drama_voice_generation_enabled:
        raise HTTPException(status_code=409, detail="音色生成功能暂未开放")


def ensure_voice_generation_task_enabled(domain: str, task_type: str) -> None:
    if domain == "drama" and task_type in VOICE_GENERATION_TASK_TYPES:
        ensure_voice_generation_enabled()
