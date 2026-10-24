"""Offline contract checks for team voice generation's task billing wrapper."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.schemas_drama import DramaVoiceGenerateRequest
from app.api.drama import generation


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [None, 91])
async def test_voice_endpoint_bills_only_team_inline_tasks(monkeypatch, team_id):
    project = DramaProject(id=17, user_id=2, team_id=team_id)
    asset = DramaAsset(id=8, project_id=17, type="voice", asset_type="audio", name="音色", url="/static/voice.mp3", params={})
    db = SimpleNamespace(get=AsyncMock(return_value=asset), commit=AsyncMock(), refresh=AsyncMock(), flush=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(generation, "get_owned_drama_project", AsyncMock(return_value=project))
    synthesize = AsyncMock(return_value=asset)
    monkeypatch.setattr(generation, "generate_voice_asset_audio", synthesize)
    async def billed(_db, _user, **kwargs):
        db.commit.assert_not_awaited()
        assert kwargs["task_type"] == "voice_synthesis"
        assert (kwargs["drama_project_id"], kwargs["asset_id"]) == (17, 8)
        return SimpleNamespace(id=5), await kwargs["executor"]()
    billing = AsyncMock(side_effect=billed)
    monkeypatch.setattr(generation, "run_billed_ephemeral", billing)
    result = await generation.generate_voice(DramaVoiceGenerateRequest(project_id=17, asset_id=8, voice_prompt="沉稳的声音"), db=db, user=User(id=2))
    assert result["asset"]["id"] == 8
    assert billing.await_count == (0 if team_id is None else 1)
    synthesize.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_voice_budget_failure_preserves_payment_status(monkeypatch):
    project = DramaProject(id=17, user_id=2, team_id=91)
    asset = DramaAsset(id=8, project_id=17, type="voice", asset_type="audio", params={})
    db = SimpleNamespace(get=AsyncMock(return_value=asset), commit=AsyncMock(), refresh=AsyncMock(), flush=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(generation, "get_owned_drama_project", AsyncMock(return_value=project))
    synthesize = AsyncMock()
    monkeypatch.setattr(generation, "generate_voice_asset_audio", synthesize)
    monkeypatch.setattr(generation, "run_billed_ephemeral", AsyncMock(side_effect=ValueError("团队余额不足，请联系所有者充值")))
    with pytest.raises(HTTPException) as exc:
        await generation.generate_voice(DramaVoiceGenerateRequest(project_id=17, asset_id=8, voice_prompt="声音"), db=db, user=User(id=2))
    assert exc.value.status_code == 402
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_team_voice_is_not_committed_before_budget_admission(monkeypatch):
    project = DramaProject(id=17, user_id=2, team_id=91)
    db = SimpleNamespace(add=Mock(), get=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(), flush=AsyncMock(), rollback=AsyncMock())
    db.add.side_effect = lambda asset: setattr(asset, "id", 8)
    monkeypatch.setattr(generation, "get_owned_drama_project", AsyncMock(return_value=project))
    async def rejected(*args, **kwargs):
        db.commit.assert_not_awaited()
        db.flush.assert_awaited()
        assert kwargs["asset_id"] == 8
        raise ValueError("团队余额不足，请联系所有者充值")
    monkeypatch.setattr(generation, "run_billed_ephemeral", rejected)
    with pytest.raises(HTTPException) as exc:
        await generation.generate_voice(DramaVoiceGenerateRequest(project_id=17, voice_prompt="新声音"), db=db, user=User(id=2))
    assert exc.value.status_code == 402
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
