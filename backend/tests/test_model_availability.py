"""Model switches must apply to catalogs and every new-call routing fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.config import Settings
from app.schemas_routing import DefaultModels, LogicalModel, LogicalModelBinding, SystemModelChannel
from app.services.logical_model_router import resolve_logical_model_candidates, resolve_upstream_model
from app.services.media_catalog import build_media_catalog, is_valid_project_media_model
from app.services.media_models.catalog import priced_catalog
from app.services.media_models.runtime import pin_route, restore_route
from app.services.model_settings import RoutingSnapshot


MODELS = {"text": "kimi-k2.6", "image": "seedream-5.0-pro",
          "video": "seedance-2.5", "audio": "qwen-tts-2025-05-22"}


@pytest.fixture
def routing(monkeypatch):
    settings = Settings(_env_file=None, debug=False, ark_mock=False, local_media_mock=False)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.media_models.runtime.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.media_models.planning.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.media_catalog.get_settings", lambda: settings)
    from app.services.media_models.runtime import current_operation, current_snapshot
    snapshot_token = current_snapshot.set(None)
    operation_token = current_operation.set(None)

    def configure(capability="image", *, enabled=True, binding_enabled=True, default=True):
        model_id = MODELS[capability]
        channel = SystemModelChannel(id="alpha", name="Alpha", base_url="https://alpha.test/v1",
            api_key="test-key", has_api_key=True, models=[model_id])
        model = LogicalModel(id=model_id, capability=capability, enabled=enabled,
            bindings=[LogicalModelBinding(channel_id=channel.id, upstream_model=model_id, enabled=binding_enabled)])
        defaults = DefaultModels(**{f"{capability}_model": model_id if default else ""})
        snapshot = RoutingSnapshot(channels=[channel], logical_models=[model], default_models=defaults)
        monkeypatch.setattr("app.services.model_settings._routing_snapshot", snapshot)
        return snapshot

    yield configure
    current_operation.reset(operation_token)
    current_snapshot.reset(snapshot_token)


@pytest.mark.parametrize("capability", MODELS)
def test_disabled_model_cannot_use_explicit_or_default_direct_channel_fallback(routing, capability):
    snapshot = routing(capability, enabled=False)
    model_id = MODELS[capability]
    assert resolve_logical_model_candidates(capability, model_id) == []
    assert resolve_logical_model_candidates(capability, "") == []
    with pytest.raises(ValueError):
        resolve_upstream_model(capability, model_id)
    assert snapshot.channels[0].enabled is True


@pytest.mark.parametrize("capability,alias", [
    ("image", " seedream-5-0-pro "), ("video", "SEEDANCE-2-5"),
    ("video", "doubao-seedance-2-5-260628"),
])
def test_disabled_media_alias_cannot_be_admitted(routing, capability, alias):
    routing(capability, enabled=False)
    with pytest.raises(ValueError):
        pin_route(capability, alias)
    assert is_valid_project_media_model(alias, capability) is False


@pytest.mark.parametrize("capability", MODELS)
def test_disabled_binding_cannot_be_replaced_by_direct_or_environment_fallback(routing, capability):
    routing(capability, binding_enabled=False)
    assert resolve_logical_model_candidates(capability, MODELS[capability]) == []
    with pytest.raises(ValueError):
        resolve_upstream_model(capability, MODELS[capability])


@pytest.mark.parametrize("capability", MODELS)
def test_managed_capability_with_empty_default_cannot_reactivate_environment_model(routing, capability):
    routing(capability, enabled=False, default=False)
    assert resolve_logical_model_candidates(capability, "") == []
    with pytest.raises(ValueError):
        resolve_upstream_model(capability, None)


def test_unmanaged_custom_model_keeps_legacy_direct_routing(routing):
    snapshot = routing(enabled=False)
    snapshot.channels[0].models.append("custom-image-model")
    candidates = resolve_logical_model_candidates("image", "custom-image-model")
    assert [route.upstream_model for route in candidates] == ["custom-image-model"]
    assert resolve_upstream_model("image", "custom-image-model") == "custom-image-model"


def test_raw_upstream_request_respects_binding_switch_on_each_channel(routing):
    snapshot = routing()
    alpha = snapshot.channels[0]
    alpha.models = ["custom-image-model"]
    beta = alpha.model_copy(update={"id": "beta", "name": "Beta"})
    snapshot.channels.append(beta)
    snapshot.logical_models = [LogicalModel(id="friendly-image", capability="image", bindings=[
        LogicalModelBinding(channel_id="alpha", upstream_model="custom-image-model", enabled=False),
        LogicalModelBinding(channel_id="beta", upstream_model="custom-image-model", enabled=True),
    ])]
    candidates = resolve_logical_model_candidates("image", "custom-image-model")
    assert [route.channel_id for route in candidates] == ["beta"]


@pytest.mark.parametrize("capability,bucket", [("image", "image_models"), ("video", "video_models")])
def test_catalog_presets_channels_and_default_cannot_reinsert_disabled_model(routing, capability, bucket):
    snapshot = routing(capability, enabled=False)
    result = build_media_catalog(logical_models=snapshot.logical_models, channels=snapshot.channels,
                                defaults=snapshot.default_models)
    assert MODELS[capability] not in {row["id"] for row in result[bucket]}
    assert result["defaults"][f"{capability}_model"] == ""


async def test_priced_catalog_also_excludes_disabled_rows(routing, monkeypatch):
    routing("image", enabled=False)
    monkeypatch.setattr("app.services.media_models.catalog.active_book", AsyncMock(return_value=None))
    result = await priced_catalog(SimpleNamespace(), {
        "image_models": [{"id": MODELS["image"]}], "video_models": [],
    })
    assert result["image_models"] == []


@pytest.mark.parametrize("logical_enabled,binding_enabled", [(False, True), (True, False)])
def test_frozen_route_continues_when_only_model_or_binding_is_disabled(
    routing, logical_enabled, binding_enabled,
):
    snapshot = routing("video", enabled=logical_enabled, binding_enabled=binding_enabled)
    saved = {"capability": "video", "logical_model_id": MODELS["video"],
             "upstream_model": MODELS["video"], "channel_id": "alpha", "channel_name": "Alpha",
             "base_url": "https://original.test/v1", "protocol": "openai", "api_format": "openai"}
    restored = restore_route(saved)
    assert restored.base_url == saved["base_url"]
    assert restored.api_key == snapshot.channels[0].api_key


@pytest.mark.parametrize("capability", MODELS)
def test_managed_model_without_enabled_bindings_cannot_switch_to_another_model(routing, capability):
    snapshot = routing(capability, binding_enabled=False)
    other = snapshot.logical_models[0].model_copy(deep=True)
    other.id = "other-model"
    other.bindings[0].upstream_model = "other-model"
    other.bindings[0].enabled = True
    snapshot.logical_models.append(other)
    snapshot.channels[0].models.append("other-model")
    assert resolve_logical_model_candidates(capability, MODELS[capability]) == []
    with pytest.raises(ValueError):
        resolve_upstream_model(capability, MODELS[capability])


@pytest.mark.parametrize("capability", MODELS)
def test_ark_without_channels_cannot_reactivate_disabled_model_from_environment(routing, capability):
    from app.services.ark import ArkGateway

    snapshot = routing(capability, enabled=False)
    snapshot.channels.clear()
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False))
    with pytest.raises(ValueError):
        gateway._resolve_ark_route(capability, MODELS[capability])


@pytest.mark.parametrize("capability", ["image", "video", "audio"])
async def test_mock_generation_respects_disabled_model_before_creating_output(routing, monkeypatch, capability):
    from app.services.ark import ArkGateway

    routing(capability, enabled=False)
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_mock=True))
    image = Mock(return_value="/static/mock.png")
    edge = AsyncMock()
    monkeypatch.setattr(gateway, "_write_mock_image", image)
    monkeypatch.setattr(gateway, "_tts_edge", edge)
    with pytest.raises(ValueError):
        if capability == "image":
            await gateway.gen_image("test", model=MODELS[capability])
        elif capability == "video":
            await gateway.gen_video_i2v("https://test/image.png", "test", 5, model=MODELS[capability])
        else:
            await gateway.tts("disabled audio", "Cherry")
    image.assert_not_called()
    edge.assert_not_called()


@pytest.mark.parametrize("method", ["_tts_openai_speech", "_tts_openai_chat_audio"])
async def test_qwen_actual_upstream_cannot_bypass_disabled_logical_audio_switch(
    routing, monkeypatch, tmp_path, method,
):
    from app.services.ark import ArkGateway

    routing("audio", enabled=False)
    assert resolve_logical_model_candidates("audio", "qwen3-omni-flash") == []
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_api_key="env-key"))
    request = AsyncMock(return_value=SimpleNamespace(status_code=500, text="fail", content=b""))
    monkeypatch.setattr(gateway, "_request_media", request)
    with pytest.raises(ValueError):
        await getattr(gateway, method)("test", "Cherry", tmp_path / "audio.mp3", model="qwen3-omni-flash")
    request.assert_not_called()


async def test_audio_request_uses_enabled_binding_credentials(routing, monkeypatch, tmp_path):
    from app.services.ark import ArkGateway

    snapshot = routing("audio")
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_api_key="env-key",
                                          ark_base_url="https://environment.test/v1"))
    request = AsyncMock(return_value=SimpleNamespace(status_code=500, text="fail", content=b""))
    monkeypatch.setattr(gateway, "_request_media", request)
    await gateway._tts_openai_chat_audio("test", "Cherry", tmp_path / "audio.mp3", model="qwen3-omni-flash")
    assert request.call_args.args[2] == snapshot.channels[0].base_url + "/chat/completions"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


async def test_disabled_audio_fallback_is_skipped_before_calling_provider(routing, monkeypatch, tmp_path):
    from app.services.ark import ArkGateway

    snapshot = routing("audio", enabled=False)
    enabled = LogicalModel(id="gemini-2.5-pro-preview-tts", capability="audio", bindings=[
        LogicalModelBinding(channel_id="alpha", upstream_model="gemini-2.5-pro-preview-tts"),
    ])
    snapshot.logical_models.append(enabled)
    snapshot.channels[0].models.append(enabled.id)
    snapshot.default_models.audio_model = enabled.id
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_mock=False, ark_api_key="env-key"))
    speech = AsyncMock(return_value=False)
    monkeypatch.setattr(gateway, "_tts_openai_speech", speech)
    monkeypatch.setattr("app.services.ark.iter_tokenfree_tts_models", lambda _: [enabled.id, "qwen3-omni-flash"])
    monkeypatch.setattr("app.services.storage.project_dir", lambda _: tmp_path)
    monkeypatch.setattr("app.services.storage.publish_local", lambda _: "/audio.mp3")
    monkeypatch.setattr("app.services.ark.is_near_silent_audio", lambda _: False)

    async def edge(_text, dest, **_kwargs):
        dest.write_bytes(b"a" * 4000)

    monkeypatch.setattr(gateway, "_tts_edge", edge)
    assert await gateway.tts("test", "Cherry") == "/audio.mp3"
    assert [call.kwargs["model"] for call in speech.call_args_list] == [enabled.id]


@pytest.mark.parametrize("method", ["expand_content", "chat_storyboard"])
async def test_mock_text_generation_cannot_bypass_disabled_model(routing, method):
    from app.services.ark import ArkGateway

    routing("text", enabled=False)
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_mock=True))
    with pytest.raises(ValueError):
        if method == "expand_content":
            await gateway.expand_content("test")
        else:
            await gateway.chat_storyboard("test", "theme", "", "", 5, 10, 5)


def test_frozen_image_route_still_resolves_after_model_is_disabled(routing):
    from app.services.ark import ArkGateway
    from app.services.media_models.runtime import current_snapshot

    snapshot = routing("image", enabled=False)
    saved = {"capability": "image", "logical_model_id": MODELS["image"],
             "upstream_model": MODELS["image"], "channel_id": "alpha", "channel_name": "Alpha",
             "base_url": "https://original.test/v1", "protocol": "openai", "api_format": "openai"}
    current_snapshot.set({"items": [{"capability": "image", "model_id": MODELS["image"],
                                    "operation_id": "image:0", "route": saved}]})
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False))
    route = gateway._resolve_ark_route("image", MODELS["image"])
    assert route.base_url == saved["base_url"]
    assert route.api_key == snapshot.channels[0].api_key


def test_image_snapshot_does_not_exempt_new_audio_from_disabled_guard(routing):
    from app.services.ark import ArkGateway
    from app.services.media_models.runtime import current_snapshot

    routing("audio", enabled=False)
    current_snapshot.set({"items": [{"capability": "image"}]})
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False))
    with pytest.raises(ValueError):
        gateway._resolve_ark_route("audio", None)


@pytest.mark.parametrize("capability,stale_model", [("image", "gpt-image-2"), ("video", "seedance-2.0")])
async def test_task_planning_cannot_use_stale_environment_model_when_managed_default_is_empty(
    routing, capability, stale_model,
):
    from app.config import get_settings
    from app.services.media_models.planning import plan_items

    snapshot = routing(capability, enabled=False, default=False)
    snapshot.channels.clear()
    setattr(get_settings(), f"model_{capability}", stale_model)
    task = SimpleNamespace(domain="studio", task_type=f"tool_{capability}", payload={},
                           asset_id=None, shot_id=None, fragment_id=None, drama_project_id=None)
    with pytest.raises(ValueError, match="默认"):
        await plan_items(None, task)
    task.payload = {"model_id": stale_model}
    items = await plan_items(None, task)
    assert items[0]["model_id"] == stale_model


@pytest.mark.parametrize("capability,upstream", [
    ("image", "doubao-seedream-5-0-pro-260628"),
    ("video", "doubao-seedance-2-5-260628"),
])
def test_frozen_model_body_resolution_continues_after_new_requests_are_disabled(routing, capability, upstream):
    from app.services.drama.build_seedance_generate_body import resolve_seedance_model_endpoint
    from app.services.drama.seedream_options import resolve_seedream_model_endpoint
    from app.services.media_models.runtime import current_snapshot

    routing(capability, enabled=False, binding_enabled=False)
    saved = {"capability": capability, "logical_model_id": MODELS[capability],
             "upstream_model": upstream, "channel_id": "alpha", "channel_name": "Alpha",
             "base_url": "https://original.test/v1", "protocol": "openai", "api_format": "openai"}
    current_snapshot.set({"items": [{"capability": capability, "model_id": MODELS[capability],
                                    "operation_id": capability + ":0", "route": saved}]})
    assert resolve_upstream_model(capability, MODELS[capability]) == upstream
    if capability == "image":
        assert resolve_seedream_model_endpoint(MODELS[capability]) == upstream
    else:
        assert resolve_seedance_model_endpoint(MODELS[capability]) == MODELS[capability]


def test_enabled_standard_seedream_is_independent_of_disabled_pro(routing):
    from app.config import get_settings
    from app.services.media_models.runtime import execution_params

    snapshot = routing("image", enabled=False)
    get_settings().model_image = MODELS["image"]
    snapshot.logical_models.append(LogicalModel(id="seedream-5.0", capability="image", bindings=[
        LogicalModelBinding(channel_id="alpha", upstream_model="seedream-5.0"),
    ]))
    snapshot.channels[0].models.append("seedream-5.0")
    snapshot.default_models.image_model = "seedream-5.0"
    assert resolve_upstream_model("image", "seedream-5.0") == "seedream-5.0"
    assert resolve_upstream_model("image", None) == "seedream-5.0"
    strategy, _, _ = execution_params("image", {"resolution": "2K"})
    assert strategy.model_id == "seedream-5.0"


async def test_custom_audio_logical_name_keeps_qwen_channel_after_upstream_alias_conversion(
    routing, monkeypatch, tmp_path,
):
    from app.services.ark import ArkGateway

    snapshot = routing("audio")
    snapshot.logical_models[0].id = "friendly-voice"
    snapshot.default_models.audio_model = "friendly-voice"
    gateway = ArkGateway(settings=Settings(_env_file=None, debug=False, ark_mock=False,
                                          local_media_mock=True, ark_api_key="env-key"))
    request = AsyncMock(return_value=SimpleNamespace(status_code=200, content=b"simulated audio"))
    monkeypatch.setattr(gateway, "_request_media", request)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _: tmp_path)
    metadata = {}
    await gateway.tts("test", "Cherry", result_metadata=metadata)
    assert request.call_count == 1
    assert request.call_args.args[2] == "https://alpha.test/v1/chat/completions"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert request.call_args.kwargs["json"]["model"] == "qwen3-omni-flash"
    assert metadata["model_id"] == "qwen3-omni-flash"
