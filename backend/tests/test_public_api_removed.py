"""Removed external generation routes and keys must never grant application access."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.deps import _user_from_bearer, get_current_user
from app.main import app
from app.schemas_tasks import TaskCreateRequest
from app.services.tasks.handlers import get_task_handler
from app.services.tasks.service import create_task


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/user/api-keys"), ("POST", "/api/user/api-keys"),
    ("DELETE", "/api/user/api-keys/1"), ("POST", "/api/v1/images/generations"),
    ("POST", "/api/v1/videos/generations"), ("POST", "/api/v1/seedance/tasks"),
    ("GET", "/api/v1/tasks/retired-task"),
])
async def test_removed_routes_are_not_reachable(method, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path)
    assert response.status_code == 404
    assert not any(route.startswith(("/api/v1/", "/api/user/api-keys")) for route in app.openapi()["paths"])


@pytest.mark.asyncio
async def test_retired_key_is_not_accepted_as_application_bearer():
    db = SimpleNamespace(execute=AsyncMock())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="pf_live_retired_test_key")
    assert await _user_from_bearer(db, creds.credentials) is None
    with pytest.raises(HTTPException) as error:
        await get_current_user(creds=creds, db=db)
    assert error.value.status_code == 401
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_login_jwt_still_resolves_application_user(monkeypatch):
    user = SimpleNamespace(id=31)
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr("app.deps.decode_token", lambda token: "31")
    monkeypatch.setattr("app.deps.get_user_by_id", lookup)
    assert await _user_from_bearer(None, "login-jwt") is user
    lookup.assert_awaited_once_with(None, 31)


@pytest.mark.asyncio
async def test_key_header_cannot_authenticate_an_application_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tasks/generations", headers={"X-Api-Key": "pf_live_retired_test_key"})
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["v1_image", "v1_video", "v1_seedance"])
async def test_generic_task_admission_cannot_recreate_removed_generations(kind):
    assert get_task_handler("api", kind) is None
    db = SimpleNamespace(add=AsyncMock(), flush=AsyncMock())
    with pytest.raises(ValueError, match="尚未接入"):
        await create_task(db, SimpleNamespace(id=1), TaskCreateRequest(domain="api", task_type=kind))
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
