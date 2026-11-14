"""Curated public styles keep legacy template storage and lookups intact."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.templates import get_template, list_templates
from app.services.creation_templates import (
    CREATION_TEMPLATE_IDS,
    DEFAULT_CREATION_TEMPLATE_ID,
    normalize_creation_template,
)
from app.services.templates_seed import TEMPLATES


def template_for(template_id: str) -> SimpleNamespace:
    return SimpleNamespace(**next(item for item in TEMPLATES if item["id"] == template_id))


def test_public_styles_have_default_vertical_ratio_without_mutating_legacy_rows():
    expected_names = ("3D 动画", "二维动漫", "绘本插画", "国风水墨")
    assert DEFAULT_CREATION_TEMPLATE_ID == "anim_3d"
    for index, (template_id, name) in enumerate(zip(CREATION_TEMPLATE_IDS, expected_names)):
        template = template_for(template_id)
        previous = vars(template).copy()
        data = normalize_creation_template(template)
        assert data["name"] == name
        assert data["default_ratio"] == "9:16"
        assert data["sort_order"] == index
        assert data["preview_cover"] == template.preview_cover
        assert data["style_prefix"] == template.style_prefix
        assert vars(template) == previous


@pytest.mark.asyncio
async def test_public_list_is_curated_and_ordered_independently_of_database_sort():
    rows = [template_for(template_id) for template_id in reversed(CREATION_TEMPLATE_IDS)]
    rows.append(template_for("huoke_douyin_hook"))
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    data = await list_templates(db)

    assert tuple(item["id"] for item in data) == CREATION_TEMPLATE_IDS
    query = db.execute.call_args.args[0]
    assert set(query.compile().params["id_1"]) == set(CREATION_TEMPLATE_IDS)
    assert "is_active IS true" in str(query)


@pytest.mark.asyncio
async def test_legacy_template_details_remain_available():
    legacy = template_for("huoke_douyin_hook")
    db = SimpleNamespace(get=AsyncMock(return_value=legacy))
    data = await get_template(legacy.id, db)
    assert data["id"] == legacy.id
    assert data["name"] == legacy.name
    assert data["seedream_config"] == legacy.seedream_config


@pytest.mark.asyncio
async def test_inactive_template_is_not_exposed_by_detail_endpoint():
    template = template_for("anim_3d")
    template.is_active = False
    db = SimpleNamespace(get=AsyncMock(return_value=template))
    with pytest.raises(HTTPException) as exc:
        await get_template(template.id, db)
    assert exc.value.status_code == 404
