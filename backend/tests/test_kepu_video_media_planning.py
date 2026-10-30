"""Short-video admission reserves the same video request as the worker, offline."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services import pipeline, storage
from app.services.media_models import planning, runtime


def _shot(no, **changes):
    return SimpleNamespace(**{
        "id": no, "shot_no": no, "duration": 5, "segment_script": "",
        "video_prompt": "", "image_url": f"/static/generated/p1/shot_{no:03d}.png",
        "image_ark_url": None, "video_url": None, "last_frame_url": None,
        "video_skip_reason": None, **changes,
    })


def _project(shots, **changes):
    return SimpleNamespace(**{
        "id": 1, "pipeline_mode": "full", "image_model": "seedream-5.0-pro",
        "video_model": "seedance-2.5", "output_ratio": "9:16", "resolution_mode": "sd",
        "video_resolution": "720p", "template": None, "shots": shots, **changes,
    })


def _db(project):
    return SimpleNamespace(execute=AsyncMock(return_value=Mock(scalar_one_or_none=lambda: project)))


def _task(kind="project_pipeline", **changes):
    return TaskRun(domain="kepu", task_type=kind, project_id=1, payload={"phase": "videos"}, **changes)


@pytest.fixture
def offline(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "seedance_duration_min", 4)
    monkeypatch.setattr(settings, "seedance_duration_max", 30)
    monkeypatch.setattr(settings, "kepu_seedance_native_audio", True)
    monkeypatch.setattr(settings, "kepu_seedance_sfx_audio", False)
    monkeypatch.setattr(planning, "pin_route", lambda capability, model: {
        "capability": capability, "logical_model_id": model, "upstream_model": model,
        "channel_id": "__runtime__", "channel_name": "Runtime", "base_url": settings.ark_base_url,
        "protocol": "openai", "api_format": "openai",
    })
    forbidden = Mock(side_effect=AssertionError("admission must not generate or publish media"))
    monkeypatch.setattr(storage, "publish_local", forbidden)
    monkeypatch.setattr(storage, "republish_url", forbidden)
    monkeypatch.setattr(pipeline, "get_ark", forbidden)
    network = AsyncMock(side_effect=AssertionError("admission must stay offline"))
    monkeypatch.setattr(httpx.AsyncClient, "send", network)
    return forbidden, network


@pytest.mark.asyncio
async def test_video_snapshot_pins_script_duration_audio_and_output_settings(offline, monkeypatch):
    shot = _shot(1, duration=5, segment_script="@duration:4 观察天空\n@duration:6 解释散射")
    project = _project([shot], output_ratio="16:9", video_resolution="1080p")
    task = _task()
    monkeypatch.setattr(planning, "active_book", AsyncMock(return_value=None))
    await planning.prepare_task_media(_db(project), task)
    saved = task.billing_snapshot["items"][0]
    assert saved["params"]["duration"] == 10
    assert saved["params"]["generate_audio"] is True
    assert saved["params"]["resolution"] == "1080p"
    assert saved["params"]["aspect_ratio"] == "16:9"
    token = runtime.current_snapshot.set(task.billing_snapshot)
    op_token = runtime.current_operation.set(None)
    try:
        _, actual, _ = runtime.execution_params("video", {
            "duration": 5, "resolution": "480p", "aspect_ratio": "9:16",
            "generate_audio": True, "reference_images": [shot.image_url],
        }, shot_no=1, model=project.video_model)
        assert actual["duration"] == 10
        assert actual["resolution"] == "1080p" and actual["aspect_ratio"] == "16:9"
    finally:
        runtime.current_snapshot.reset(token)
        runtime.current_operation.reset(op_token)
    offline[0].assert_not_called()
    offline[1].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,expected", [
    ({"video_prompt": "@duration:6 第一段\n@duration:6 第二段"}, 12),
    ({"duration": 2}, 4),
    ({"duration": 80}, 30),
    ({"duration": 8.4}, 8),
])
async def test_video_snapshot_uses_legacy_script_and_worker_fallback_clamp(offline, changes, expected):
    items = await planning.plan_items(_db(_project([_shot(1, **changes)])), _task())
    assert items[0]["params"]["duration"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("native,sfx,expected", [(True, False, True), (False, True, True), (False, False, False)])
async def test_video_snapshot_tracks_native_and_effect_audio(offline, monkeypatch, native, sfx, expected):
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", native)
    monkeypatch.setattr(get_settings(), "kepu_seedance_sfx_audio", sfx)
    items = await planning.plan_items(_db(_project([_shot(1)])), _task())
    assert items[0]["params"]["generate_audio"] is expected


@pytest.mark.asyncio
async def test_video_references_prefer_saved_image_and_reserve_generated_previous_tail(offline):
    first = _shot(1, image_ark_url="https://www.tokenfree.com/v1/tasks/t1/artifacts/image-0/content")
    second = _shot(2)
    items = await planning.plan_items(_db(_project([second, first])), _task())
    assert [item["shot_no"] for item in items] == [1, 2]
    assert items[0]["params"]["reference_images"] == [first.image_url]
    refs = items[1]["params"]["reference_images"]
    assert refs[0] == second.image_url and len(refs) == 2
    assert refs[1].startswith("pending://")
    assert not any("tokenfree.com" in ref for item in items for ref in item["params"]["reference_images"])
    offline[0].assert_not_called()


@pytest.mark.asyncio
async def test_video_references_reuse_ready_previous_tail_without_regenerating_it(offline):
    first = _shot(1, video_url="/static/generated/p1/shot_001.mp4", last_frame_url="/static/generated/p1/tail.jpg")
    second = _shot(2)
    items = await planning.plan_items(_db(_project([first, second])), _task())
    assert len(items) == 1
    assert items[0]["params"]["reference_images"] == [second.image_url, first.last_frame_url]


@pytest.mark.asyncio
async def test_video_references_reserve_frame_extraction_without_reading_existing_video(offline):
    first = _shot(1, video_url="/static/generated/p1/shot_001.mp4")
    second = _shot(2)
    items = await planning.plan_items(_db(_project([first, second])), _task())
    assert len(items) == 1
    refs = items[0]["params"]["reference_images"]
    assert refs[0] == second.image_url and len(refs) == 2 and refs[1].startswith("pending://")
    offline[0].assert_not_called()


@pytest.mark.asyncio
async def test_video_references_never_snapshot_tokenfree_authenticated_urls(offline):
    private = "https://www.tokenfree.com/v1/tasks/t1/artifacts/image-0/content"
    first = _shot(1, image_url=private, image_ark_url="https://cdn.example.com/first.png")
    second = _shot(2, image_url=private, image_ark_url=private)
    items = await planning.plan_items(_db(_project([first, second])), _task())
    assert items[0]["params"]["reference_images"] == [first.image_ark_url]
    assert items[1]["params"]["reference_images"][0].startswith("pending://")
    assert not any("tokenfree.com" in ref for item in items for ref in item["params"]["reference_images"])


@pytest.mark.asyncio
async def test_video_regen_reserves_prior_usable_still_and_replaces_only_requested_shot(offline):
    first = _shot(1, video_skip_reason="privacy", image_ark_url="https://www.tokenfree.com/v1/tasks/t1/artifacts/image-0/content")
    empty = _shot(2, image_url=None)
    third = _shot(3, video_url="/static/generated/p1/old.mp4")
    items = await planning.plan_items(_db(_project([third, empty, first])), _task("shot_regen_video", shot_id=3))
    assert len(items) == 1 and items[0]["target_id"] == 3
    assert items[0]["params"]["reference_images"] == [third.image_url, first.image_url]


@pytest.mark.asyncio
async def test_video_reference_cap_rejected_before_provider_or_publication(offline, monkeypatch):
    base_resolve = planning.resolve_strategy
    monkeypatch.setattr(planning, "resolve_strategy", lambda model: base_resolve(model).with_reference_limits({"images": 1, "audios": 0, "videos": 0}))
    with pytest.raises(ValueError, match="reference_images"):
        await planning.plan_items(_db(_project([_shot(1), _shot(2)])), _task())
    offline[0].assert_not_called()
    offline[1].assert_not_awaited()
