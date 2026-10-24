from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models_tasks import TaskRun
from app.services.media_models.catalog import priced_catalog
from app.services.media_models.planning import plan_items
from app.services.media_models.prices import initial_draft, publish_book, validate_prices


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,expected", [("assets", [("image", 2)]), ("videos", [("video", 1), ("video", 2)]), ("script", [])])
async def test_pipeline_plans_only_missing_outputs_with_per_shot_identity(phase, expected):
    project = SimpleNamespace(id=1, image_model="seedream-5.0-pro", video_model="seedance-2.5",
        output_ratio="9:16", resolution_mode="hd", template=None, shots=[
            SimpleNamespace(id=1, shot_no=1, duration=5, image_url="saved.png", video_url=None),
            SimpleNamespace(id=2, shot_no=2, duration=8, image_url=None, video_url=None),
        ])
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(scalar_one_or_none=lambda: project)))
    task = TaskRun(domain="kepu", task_type="project_pipeline", project_id=1, payload={"phase": phase})
    items = await plan_items(db, task)
    assert [(item["capability"], item["target_id"]) for item in items] == expected
    if phase == "videos":
        assert [item["params"]["duration"] for item in items] == [5, 8]
        assert len({item["operation_id"] for item in items}) == 2


@pytest.mark.asyncio
async def test_composite_tool_reserves_video_and_helper_image():
    task = TaskRun(domain="studio", task_type="tool_video", payload={"tool_id": "t2v", "model_id": "seedance-2.5", "duration": 5, "resolution": "1080p"})
    items = await plan_items(None, task)
    assert [item["capability"] for item in items] == ["video", "image"]
    assert items[0]["params"]["resolution"] == "1080p"
    assert items[1]["params"]["resolution"] == "2K"


@pytest.mark.asyncio
async def test_catalog_removes_unknown_strategies_and_hides_unpriced_tiers(db_session):
    await publish_book(db_session, prices={"seedream-5.0-pro": {"1K": "0.15"}}, expected_version=None, user_id=1)
    result = await priced_catalog(db_session, {"image_models": [{"id": "seedream-5.0-pro"}, {"id": "unknown"}], "video_models": [{"id": "seedance-2.5"}]})
    assert len(result["image_models"]) == 1
    assert result["image_models"][0]["available_resolutions"] == ["1K"]
    assert result["video_models"][0]["available"] is False
    assert result["billing_mode"] == "fixed_media"


def test_draft_is_valid_and_does_not_publish():
    draft = initial_draft()
    assert len(draft) == 9
    assert validate_prices(draft) == draft
