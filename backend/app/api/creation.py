"""Owner-scoped, resumable conversational short-video creation."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import AsyncSessionLocal, get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ProjectOut
from app.schemas_creation import CreationMessageCreate, CreationSessionCreate, CreationSessionListOut, CreationSessionOut
from app.services import creation
from app.services.billing.http import http_exception_for_value_error

router = APIRouter(prefix="/creation-sessions", tags=["creation"])


@router.post("", response_model=CreationSessionOut)
async def create(body: CreationSessionCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return await creation.create_session(db, user, body)
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc


@router.get("", response_model=CreationSessionListOut)
async def list_recent(limit: int = Query(5, ge=1, le=20), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await creation.list_sessions(db, user.id, limit)


@router.get("/{session_id}", response_model=CreationSessionOut)
async def get(session_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await creation.session_snapshot(db, session_id, user.id)


@router.post("/{session_id}/messages", response_model=CreationSessionOut)
async def message(session_id: int, body: CreationMessageCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return await creation.add_message(db, user, session_id, body)
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc


@router.post("/{session_id}/cancel", response_model=CreationSessionOut)
async def cancel(session_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await creation.cancel_session(db, user, session_id)


@router.post("/{session_id}/retry", response_model=CreationSessionOut)
async def retry(session_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return await creation.retry_session(db, user, session_id)
    except ValueError as exc:
        await db.rollback()
        raise http_exception_for_value_error(exc) from exc


@router.post("/{session_id}/open-editor", response_model=ProjectOut)
async def open_editor(session_id: int, project_id: int | None = Query(None, ge=1),
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await creation.open_editor(db, user, session_id, project_id=project_id)


@router.get("/{session_id}/events")
async def events(session_id: int, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    user_id = int(user.id)
    initial = await creation.session_snapshot(db, session_id, user_id)
    await db.rollback()  # Do not retain a request transaction for an SSE connection.

    async def stream():
        previous = initial.model_dump_json()
        yield {"event": "session", "data": previous}
        while not await request.is_disconnected():
            await asyncio.sleep(1.5)
            async with AsyncSessionLocal() as current_db:
                snapshot = await creation.session_snapshot(current_db, session_id, user_id)
            serialized = snapshot.model_dump_json()
            if serialized != previous:
                previous = serialized
                yield {"event": "session", "data": serialized}

    return EventSourceResponse(stream(), ping=15)
