"""Temporarily closed voice design must reject new work before any side effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.drama import generation
from app.config import Settings, get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.schemas_tasks import TaskCreateRequest
from app.services.billing import ephemeral
from app.services.tasks import service


UNAVAILABLE = "音色生成功能暂未开放"


@pytest.fixture
def voice_api(monkeypatch):
    settings = get_settings()
    monkeypatch.setitem(settings.__dict__, "drama_voice_generation_enabled", False)
    user = User(id=2)
    project = DramaProject(id=17, user_id=user.id, team_id=None)
    asset = DramaAsset(id=8, project_id=17, type="voice", asset_type="audio",
                       name="已有音色", url="/static/existing.mp3", params={})
    db = SimpleNamespace(get=AsyncMock(return_value=asset), add=Mock(), flush=AsyncMock(),
                         commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock())
    db.add.side_effect = lambda value: setattr(value, "id", 8)
    lookup = AsyncMock(return_value=project)
    monkeypatch.setattr(generation, "get_owned_drama_project", lookup)
    prompt = AsyncMock(return_value=("沉稳的声音", "Cherry", "试听句"))
    synthesize = AsyncMock(return_value=asset)
    monkeypatch.setattr(generation, "suggest_voice_prompt_for_character", prompt)
    monkeypatch.setattr(generation, "generate_voice_asset_audio", synthesize)

    async def register(_db, _user, **kwargs):
        if kwargs["task_type"] == "voice_prompt":
            return SimpleNamespace(id=1), ("沉稳的声音", "Cherry", "试听句")
        return SimpleNamespace(id=1), asset

    billing = AsyncMock(side_effect=register)
    monkeypatch.setattr(generation, "run_billed_ephemeral", billing)
    app = FastAPI()
    app.include_router(generation.router, prefix="/api/drama")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(app=app, settings=settings, project=project, asset=asset, db=db,
                           lookup=lookup, prompt=prompt, synthesize=synthesize, billing=billing)


def test_voice_generation_defaults_to_closed(monkeypatch):
    monkeypatch.delenv("DRAMA_VOICE_GENERATION_ENABLED", raising=False)
    assert Settings(_env_file=None, debug=False).drama_voice_generation_enabled is False


@pytest.mark.parametrize("team_id", [None, 91])
@pytest.mark.parametrize("path,payload", [
    ("voice_prompt", {"asset_id": 8}),
    ("voice", {"asset_id": 8, "voice_prompt": "沉稳的声音"}),
    ("voice", {"voice_prompt": "新的声音"}),
])
async def test_closed_voice_endpoints_reject_before_assets_tasks_billing_or_upstream(
    voice_api, team_id, path, payload,
):
    api = voice_api
    api.project.team_id = team_id
    if path == "voice_prompt":
        api.asset.type = "character"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(api.app), base_url="http://test") as client:
        response = await client.post(f"/api/drama/generation/{path}", json={"project_id": 17, **payload})
    assert response.status_code == 409
    assert response.json() == {"detail": UNAVAILABLE}
    api.lookup.assert_not_awaited()
    api.db.get.assert_not_awaited()
    api.db.add.assert_not_called()
    api.db.flush.assert_not_awaited()
    api.db.commit.assert_not_awaited()
    api.billing.assert_not_awaited()
    api.prompt.assert_not_awaited()
    api.synthesize.assert_not_awaited()
    assert api.asset.params == {} and api.asset.url == "/static/existing.mp3"


@pytest.mark.parametrize("task_type", ["voice_prompt", "voice_synthesis"])
@pytest.mark.parametrize("entry", ["queued", "inline"])
async def test_closed_voice_generation_cannot_bypass_api_via_task_admission(voice_api, monkeypatch, task_type, entry):
    scope = AsyncMock(side_effect=AssertionError("closed requests must not reach task admission"))
    monkeypatch.setattr(service, "_validate_task_scope", scope)
    with pytest.raises(HTTPException) as error:
        if entry == "queued":
            await service.create_task(voice_api.db, User(id=2), TaskCreateRequest(domain="drama", task_type=task_type))
        else:
            await ephemeral.create_ephemeral_task_row(voice_api.db, User(id=2), domain="drama", task_type=task_type)
    assert error.value.status_code == 409 and error.value.detail == UNAVAILABLE
    scope.assert_not_awaited()
    voice_api.db.add.assert_not_called()
    voice_api.db.commit.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
async def test_public_capabilities_exposes_voice_switch_without_authentication_or_database(monkeypatch, enabled):
    from app.main import app

    monkeypatch.setitem(get_settings().__dict__, "drama_voice_generation_enabled", enabled)
    database = Mock(side_effect=AssertionError("public capabilities must not open a database session"))
    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {"drama_voice_generation_enabled": enabled}
    database.assert_not_called()


async def test_voice_request_already_admitted_can_finish_after_switch_closes(voice_api, monkeypatch):
    api = voice_api
    monkeypatch.setitem(api.settings.__dict__, "drama_voice_generation_enabled", True)

    async def register(_db, _user, **kwargs):
        monkeypatch.setitem(api.settings.__dict__, "drama_voice_generation_enabled", False)
        return SimpleNamespace(id=1), await kwargs["executor"]()

    api.billing.side_effect = register
    async with httpx.AsyncClient(transport=httpx.ASGITransport(api.app), base_url="http://test") as client:
        response = await client.post("/api/drama/generation/voice", json={
            "project_id": 17, "asset_id": 8, "voice_prompt": "沉稳的声音",
        })
    assert response.status_code == 200
    assert response.json()["asset"]["url"] == "/static/existing.mp3"
    api.synthesize.assert_awaited_once()
