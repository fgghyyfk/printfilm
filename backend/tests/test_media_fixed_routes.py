from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.models_tasks import TaskRun
from app.schemas_routing import SystemModelChannel
from app.services.ark import ArkGateway
from app.services.billing.context import billing_scope
from app.services.media_models import get_strategy
from app.services.media_models.runtime import current_operation, operation_for
from conftest import make_user


def operation():
    strategy = get_strategy("seedance-2.5")
    return {"operation_id": "video:0", **strategy.quote({"duration": 5, "resolution": "720p"},
            {strategy.model_id: {"720p": "0.5"}}), "route": {
        "capability": "video", "logical_model_id": strategy.model_id,
        "upstream_model": "doubao-seedance-2-5-260628", "channel_id": "alpha", "channel_name": "Alpha",
        "base_url": "https://alpha.test/v1", "protocol": "openai", "api_format": "openai",
    }}


@pytest.fixture
def gateway(monkeypatch):
    alpha = SystemModelChannel(id="alpha", name="Alpha", base_url="https://changed.test/v1", api_key="alpha-key")
    beta = SystemModelChannel(id="beta", name="Beta", base_url="https://beta.test/v1", api_key="beta-key")
    monkeypatch.setattr("app.services.media_models.runtime.get_routing_snapshot", lambda: SimpleNamespace(channels=[alpha, beta]))
    result = ArkGateway(Settings(ark_mock=False, local_media_mock=False))
    monkeypatch.setattr(result, "_ark_api_key", lambda: "global-wrong-key")
    return result, alpha


@pytest.mark.asyncio
async def test_submit_failure_never_tries_second_channel_or_model(gateway, monkeypatch):
    ark, alpha = gateway
    request = AsyncMock(return_value=httpx.Response(503, text="unavailable"))
    monkeypatch.setattr(ark, "_request_media", request)
    async with billing_scope(None, snapshot={"items": [operation()]}):
        with pytest.raises(RuntimeError, match="503"):
            await ark.gen_video_seedance_body({"model": "seedance-2.5", "duration": 5, "content": [{"type": "text", "text": "test"}]})
    request.assert_awaited_once()
    assert request.await_args.args[2] == "https://alpha.test/v1/videos"
    assert request.await_args.kwargs["headers"]["Authorization"] == "Bearer alpha-key"
    assert request.await_args.kwargs["json"]["model"] == "doubao-seedance-2-5-260628"
    assert "api_key" not in operation()["route"]


@pytest.mark.asyncio
async def test_poll_uses_saved_address_and_credentials_even_when_defaults_change(gateway, monkeypatch):
    ark, alpha = gateway
    request = AsyncMock(return_value=httpx.Response(200, json={"id": "original-id", "status": "completed"}))
    monkeypatch.setattr("app.services.ark.observed_request", request)
    async with billing_scope(None, snapshot={"items": [operation()]}):
        result = await ark.fetch_task_once("original-id")
    assert request.await_args.args[2] == "https://alpha.test/v1/videos/original-id"
    assert request.await_args.kwargs["headers"]["Authorization"] == "Bearer alpha-key"
    assert result.url == "https://alpha.test/v1/videos/original-id/content"


@pytest.mark.asyncio
async def test_disabled_pinned_channel_fails_without_network(gateway, monkeypatch):
    ark, alpha = gateway
    alpha.enabled = False
    request = AsyncMock()
    monkeypatch.setattr("app.services.ark.observed_request", request)
    async with billing_scope(None, snapshot={"items": [operation()]}):
        with pytest.raises(RuntimeError, match="不会自动切换"):
            await ark.fetch_task_once("original-id")
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_reuses_accepted_provider_id_without_submit(gateway, db_session, monkeypatch):
    ark, alpha = gateway
    user = await make_user(db_session)
    op = operation()
    task = TaskRun(domain="api", task_type="v1_video", requested_by=user.id,
                   billing_snapshot={"items": [op]}, media_provider_tasks={op["operation_id"]: "accepted-id"})
    db_session.add(task)
    await db_session.flush()
    @asynccontextmanager
    async def sessions():
        yield db_session
    monkeypatch.setattr("app.database.AsyncSessionLocal", sessions)
    request = AsyncMock()
    monkeypatch.setattr(ark, "_request_media", request)
    async with billing_scope(task.id, snapshot=task.billing_snapshot):
        assert await ark.gen_video_seedance_body({"model": "seedance-2.5", "duration": 5}) == "accepted-id"
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_ids_do_not_collide_with_shot_numbers():
    first, second = operation(), operation()
    first.update(operation_id="video:2", target_id=2, shot_no=1)
    second.update(operation_id="video:3", target_id=3, shot_no=2)
    async with billing_scope(None, snapshot={"items": [first, second]}):
        assert operation_for("video", target=2)["operation_id"] == "video:2"
        assert operation_for("video", shot_no=2)["operation_id"] == "video:3"
