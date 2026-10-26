"""Reference caps apply to the complete request before admission and again at execution."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.models_tasks import TaskRun
from app.schemas_routing import AdminRoutingSettingsPatch, MediaReferenceLimits, SystemModelChannelIn
from app.schemas_tasks import TaskCreateRequest
from app.services import model_settings
from app.services.drama.build_seedance_generate_body import (
    build_seedance_content_items, build_seedance_reference_catalog, describe_seedance_content_slots,
)
from app.services.media_models import MediaValidationError, get_strategy, strategies
from app.services.media_models.planning import plan_items, prepare_task_media
from app.services.media_models.prices import resolve_strategy
from app.services.media_models.reference_limits import reference_limit_settings
from app.services.media_models.runtime import current_snapshot, execution_params


@pytest.fixture
def configured(monkeypatch):
    limits = {"seedance-2.5": {"images": 2, "videos": 1, "audios": 1},
              "seedream-5.0-pro": {"images": 1, "videos": 0, "audios": 0}}
    snapshot = model_settings.RoutingSnapshot([], [], model_settings.DefaultModels(), limits)
    monkeypatch.setattr(model_settings, "_routing_snapshot", snapshot)
    return snapshot


@pytest.mark.parametrize("strategy", strategies(), ids=lambda strategy: strategy.model_id)
@pytest.mark.parametrize("kind", ["images", "videos", "audios"])
def test_each_model_accepts_boundary_and_rejects_one_more(strategy, kind):
    maximum = strategy.describe()["reference_limits"][kind]
    params = {f"reference_{kind}": ["https://example.com/ref"] * maximum}
    strategy.normalize_and_validate(params)
    params[f"reference_{kind}"].append("https://example.com/extra")
    with pytest.raises(MediaValidationError, match=strategy.model_id):
        strategy.normalize_and_validate(params)


@pytest.mark.parametrize("invalid", [-1, 1.5, True, "3", 101, None])
def test_admin_limits_require_nonnegative_integers(invalid):
    with pytest.raises(ValidationError):
        MediaReferenceLimits(images=invalid, videos=0, audios=0)


def test_configuration_has_every_model_and_rejects_unknown_or_unsupported():
    assert set(reference_limit_settings()) == {strategy.model_id for strategy in strategies()}
    with pytest.raises(ValueError):
        reference_limit_settings({"unknown": {"images": 1, "videos": 0, "audios": 0}})
    with pytest.raises(ValueError, match="仅支持参考图片"):
        reference_limit_settings({"seedream-5.0-pro": {"images": 1, "videos": 1, "audios": 0}})


def test_alias_resolves_config_without_mutating_defaults(configured):
    assert resolve_strategy("doubao-seedance-2-5-260628").max_reference_images == 2
    assert get_strategy("seedance-2.5").max_reference_images == 9
    assert resolve_strategy("seedance-2.0").max_reference_images == 9


@pytest.mark.parametrize("params,field", [
    ({"reference_images": ["a"], "ref_urls": ["b"], "style_ref_urls": ["c"]}, "reference_images"),
    ({"image_url": "a", "last_frame": "b", "style_ref_urls": ["c"]}, "reference_images"),
    ({"style_ref_urls": ["a"], "content": [{"type": "image_url"}] * 2}, "reference_images"),
    ({"reference_audios": ["a"], "content": [{"type": "audio_url"}]}, "reference_audios"),
    ({"reference_videos": ["a"], "content": [{"type": "video_url"}]}, "reference_videos"),
])
def test_all_reference_encodings_share_one_limit(configured, params, field):
    with pytest.raises(MediaValidationError, match=field):
        resolve_strategy("seedance-2.5").normalize_and_validate(params)


def test_normalization_is_idempotent_with_alias_urls(configured):
    strategy = resolve_strategy("seedance-2.5")
    normalized = strategy.normalize_and_validate({"ref_urls": ["a"], "image_url": "b"})
    assert strategy.normalize_and_validate(normalized) == normalized


def test_image_upload_count_uses_config_instead_of_old_global_cap(configured):
    from app.services.studio_generation import validate_generation
    configured.media_reference_limits["seedream-5.0-pro"]["images"] = 10
    assert validate_generation("i2i", "Prompt", {"model_id": "seedream-5.0-pro"}, 10) == "image"
    with pytest.raises(MediaValidationError, match="当前 11 个"):
        validate_generation("i2i", "Prompt", {"model_id": "seedream-5.0-pro"}, 11)


@pytest.mark.asyncio
async def test_reference_video_url_is_resolved_before_provider_request(monkeypatch):
    from app.config import Settings
    from app.services.ark import ArkGateway
    ark = ArkGateway(Settings(ark_mock=True))
    resolve = AsyncMock(return_value="https://media.example.com/video.mp4")
    monkeypatch.setattr(ark, "_resolve_media_ref", resolve)
    result = await ark._resolve_seedance_content_items([
        {"type": "video_url", "video_url": {"url": "/static/source.mp4"}, "role": "reference_video"}])
    assert result[0]["video_url"]["url"] == "https://media.example.com/video.mp4"
    assert result[0]["role"] == "reference_video"
    resolve.assert_awaited_once_with("/static/source.mp4", prefer_https=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type,payload", [
    ("v1_image", {"model": "seedream-5.0-pro", "reference_images": ["a", "b"]}),
    ("v1_video", {"model": "seedance-2.5", "reference_audios": ["a", "b"]}),
    ("v1_seedance", {"model": "seedance-2.5", "prepared": {"seedance_body": {
        "content": [{"type": "video_url", "video_url": {"url": "a"}}] * 2}}}),
])
async def test_direct_task_requests_are_rejected_during_planning(configured, task_type, payload):
    with pytest.raises(MediaValidationError):
        await plan_items(None, TaskRun(domain="api", task_type=task_type, payload=payload))


@pytest.mark.asyncio
async def test_zero_image_limit_blocks_single_image_api_request(configured):
    configured.media_reference_limits["seedream-5.0-pro"]["images"] = 0
    with pytest.raises(MediaValidationError, match="不支持参考图片"):
        await plan_items(None, TaskRun(domain="api", task_type="v1_image",
            payload={"model": "seedream-5.0-pro", "image_url": "https://example.com/a.png"}))


@pytest.mark.asyncio
async def test_canvas_counts_linked_media_and_style_board_before_admission(configured, monkeypatch):
    project = DramaProject(id=10, params={})
    assets = {
        2: DramaAsset(id=2, project_id=10, type="image", asset_type="image", name="Image", url="https://a.png", params={}),
        3: DramaAsset(id=3, project_id=10, type="audio", asset_type="audio", name="Audio", url="https://a.mp3", params={}),
        4: DramaAsset(id=4, project_id=10, type="video", asset_type="video", name="Video", url="https://a.mp4", cover="https://poster.png", params={}),
        5: DramaAsset(id=5, project_id=99, type="image", url="https://foreign.png", params={}),
    }
    async def get(model, identity):
        return project if model is DramaProject else assets.get(identity)
    db = SimpleNamespace(get=AsyncMock(side_effect=get))
    monkeypatch.setattr("app.services.drama.image_styles.resolve_image_style_board_url", lambda _: "https://board.png")
    task = TaskRun(domain="drama", task_type="asset_video", drama_project_id=10, asset_id=1,
        payload={"model_id": "seedance-2.5", "prompt": "@asset:2 @asset:3 @asset:4",
                 "reference_asset_ids": [1, 2, 3, 4, 5, 2]})
    items = await plan_items(db, task)
    slots = items[0]["params"]["content"]
    assert [item["type"] for item in slots] == ["text", "image_url", "image_url", "audio_url", "video_url"]
    assert items[0]["reference_limits"] == {"images": 2, "videos": 1, "audios": 1}
    configured.media_reference_limits["seedance-2.5"]["images"] = 1
    with pytest.raises(MediaValidationError, match="当前 2 个"):
        await plan_items(db, task)
    task.payload = {**task.payload, "prepared": {"seedance_body": {"content": []}}}
    with pytest.raises(MediaValidationError, match="当前 2 个"):
        await plan_items(db, task)


def test_video_reference_preserves_media_kind_mentions_and_labels():
    refs = [{"id": 1, "type": "video", "assetType": "video", "name": "Sample",
             "url": "https://a.mp4", "cover": "https://poster.png"}]
    catalog = build_seedance_reference_catalog(refs)
    assert len(catalog.videos) == 1 and not catalog.images
    items = build_seedance_content_items("Follow @asset:1", refs)
    assert "@视频1" in items[0]["text"]
    assert items[1] == {"type": "video_url", "video_url": {"url": "https://a.mp4"}, "role": "reference_video"}
    assert len(describe_seedance_content_slots(refs, content="Follow @asset:1")) == len(items)


def test_execution_keeps_admitted_limits_after_config_changes(configured, monkeypatch):
    monkeypatch.setattr("app.services.media_models.runtime.restore_route", lambda route: route)
    token = current_snapshot.set({"items": [{"operation_id": "video:0", "capability": "video",
        "model_id": "seedance-2.5", "strategy_id": "seedance-2.5", "params": {}, "route": {},
        "reference_limits": {"images": 1, "videos": 0, "audios": 0}}]})
    try:
        with pytest.raises(MediaValidationError, match="reference_images"):
            execution_params("video", {"reference_images": ["a", "b"]})
    finally:
        current_snapshot.reset(token)


@pytest.mark.asyncio
async def test_rejected_task_does_not_insert_or_check_balance(configured, monkeypatch):
    from app.services.tasks import service
    monkeypatch.setattr(service, "_validate_task_scope", AsyncMock())
    monkeypatch.setattr(service, "get_task_handler", lambda *_: Mock())
    balance = AsyncMock()
    monkeypatch.setattr("app.services.billing.settlement.ensure_balance_for_task", balance)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    body = TaskCreateRequest(domain="api", task_type="v1_image",
        payload={"model": "seedream-5.0-pro", "reference_images": ["a", "b"]})
    with pytest.raises(MediaValidationError):
        await service.create_task(db, User(id=1), body)
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_media_does_not_read_price_book(configured):
    db = SimpleNamespace(execute=AsyncMock())
    task = TaskRun(domain="api", task_type="v1_video", payload={"model": "seedance-2.5", "reference_videos": ["a", "b"]})
    with pytest.raises(MediaValidationError):
        await prepare_task_media(db, task)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_limits_persist_and_reload(db_session, monkeypatch):
    monkeypatch.setattr(model_settings, "_routing_snapshot", model_settings._routing_snapshot)
    monkeypatch.setattr(model_settings, "_overlay", dict(model_settings._overlay))
    try:
        out, applied = await model_settings.patch_admin_routing_settings(db_session,
            AdminRoutingSettingsPatch(
                system_channels=[SystemModelChannelIn(id="tokenfree", name="TokenFree",
                    base_url="https://www.tokenfree.com/v1", api_key="test-only-key")],
                media_reference_limits={"seedance-2.5": MediaReferenceLimits(images=2, videos=1, audios=0)}))
        assert "media_reference_limits" in applied
        assert out.media_reference_limits["seedance-2.5"].audios == 0
        assert resolve_strategy("seedance-2.5").max_reference_images == 2
        await model_settings.load_model_settings_cache(db_session)
        assert resolve_strategy("seedance-2.5").max_reference_audios == 0
        from app.services.media_models.catalog import priced_catalog
        catalog = await priced_catalog(db_session, {"image_models": [], "video_models": [{"id": "seedance-2.5"}]})
        assert catalog["video_models"][0]["reference_limits"] == {"images": 2, "videos": 1, "audios": 0}
        await model_settings.patch_admin_routing_settings(db_session,
            AdminRoutingSettingsPatch(media_reference_limits={"gpt-image-2": MediaReferenceLimits(images=0, videos=0, audios=0)}))
        assert resolve_strategy("seedance-2.5").max_reference_images == 2
    finally:
        from app.config import get_settings
        get_settings.cache_clear()
