"""Retired conversation endpoints and the retained legacy-project handoff."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api import creation as creation_api
from app.database import get_db
from app.deps import get_current_user
from app.schemas import ProjectOut


@pytest.fixture
def legacy_api():
    app = FastAPI()
    app.include_router(creation_api.router, prefix="/api")
    db = object()

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    return app, db


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/creation-sessions"),
    ("GET", "/api/creation-sessions"),
    ("GET", "/api/creation-sessions/42"),
    ("POST", "/api/creation-sessions/42/messages"),
    ("POST", "/api/creation-sessions/42/cancel"),
    ("POST", "/api/creation-sessions/42/retry"),
    ("GET", "/api/creation-sessions/42/events"),
])
async def test_retired_conversation_endpoints_return_not_found(legacy_api, method, path):
    app, _db = legacy_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        response = await client.request(method, path)
    assert response.status_code == 404


def test_only_legacy_project_handoff_is_registered_and_documented():
    from app.main import app

    paths = app.openapi()["paths"]
    creation_paths = {path for path in paths if path.startswith("/api/creation-sessions")}
    assert creation_paths == {"/api/creation-sessions/{session_id}/open-editor"}
    operation = paths["/api/creation-sessions/{session_id}/open-editor"]["post"]
    assert operation["tags"] == ["科普项目与作品"]
    assert "历史" in operation["description"]
    assert operation["security"]


async def test_legacy_handoff_requires_authentication(legacy_api, monkeypatch):
    app, _db = legacy_api
    handoff = AsyncMock()
    monkeypatch.setattr(creation_api.creation, "open_editor", handoff)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        response = await client.post("/api/creation-sessions/42/open-editor")
    assert response.status_code == 401
    handoff.assert_not_awaited()


@pytest.mark.parametrize("project_id", [None, 73])
async def test_legacy_handoff_forwards_owner_and_selected_project(legacy_api, monkeypatch, project_id):
    app, db = legacy_api
    user = SimpleNamespace(id=27)
    app.dependency_overrides[get_current_user] = lambda: user
    now = datetime.now(UTC)
    project = ProjectOut(
        id=73, template_id="3d_cute", title="历史作品", source_type="script", source_text="历史文案",
        status="draft", progress=0, error_msg=None, cover_url=None, final_video_url=None,
        resolution_mode="hd", ref_image_url=None, created_at=now, updated_at=now,
    )
    handoff = AsyncMock(return_value=project)
    monkeypatch.setattr(creation_api.creation, "open_editor", handoff)
    params = {} if project_id is None else {"project_id": project_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        response = await client.post("/api/creation-sessions/42/open-editor", params=params)
    assert response.status_code == 200
    assert response.json()["id"] == project.id
    handoff.assert_awaited_once_with(db, user, 42, project_id=project_id)


@pytest.mark.parametrize("status_code,detail,headers", [
    (404, "该创作版本不存在", None),
    (409, "正在停止旧创作任务并结算，请稍后重试进入编辑", {"Retry-After": "2"}),
])
async def test_legacy_handoff_preserves_owner_and_settlement_errors(
    legacy_api, monkeypatch, status_code, detail, headers,
):
    app, _db = legacy_api
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=27)
    handoff = AsyncMock(side_effect=HTTPException(status_code, detail=detail, headers=headers))
    monkeypatch.setattr(creation_api.creation, "open_editor", handoff)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        response = await client.post("/api/creation-sessions/42/open-editor?project_id=73")
    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    if headers:
        assert response.headers["retry-after"] == "2"
