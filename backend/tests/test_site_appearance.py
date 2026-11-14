# -*- coding: utf-8 -*-
"""全站主题：公开读取、默认值、管理员切换和非法值。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from app.api.admin.settings import SiteAppearanceBody, admin_get_appearance, admin_set_appearance
from app.api.site import site_appearance
from app.deps import get_current_admin
from app.main import app
from app.models_settings import AppSettings
from app.services.site_appearance import DEFAULT_SITE_THEME, SITE_THEMES, get_site_theme, set_site_theme



async def test_default_and_switch(db_session):
    assert await get_site_theme(db_session) == DEFAULT_SITE_THEME
    await set_site_theme(db_session, "studio")
    assert await get_site_theme(db_session) == "studio"
    assert (await site_appearance(Response(), db=db_session)).theme == "studio"


async def test_retired_theme_falls_back(db_session):
    db_session.add(AppSettings(id="appearance", config_json={"theme": "gone"}))
    await db_session.flush()
    assert await get_site_theme(db_session) == DEFAULT_SITE_THEME


async def test_does_not_touch_model_settings_row(db_session):
    db_session.add(AppSettings(id="default", config_json={"flat": {"x": 1}}))
    await db_session.flush()
    await set_site_theme(db_session, "studio")
    assert (await db_session.get(AppSettings, "default")).config_json == {"flat": {"x": 1}}


async def test_admin_endpoints(db_session):
    out = await admin_set_appearance(SiteAppearanceBody(theme="studio"), _admin=None, db=db_session)
    assert out.theme == "studio" and out.themes == list(SITE_THEMES)
    assert (await admin_get_appearance(_admin=None, db=db_session)).theme == "studio"


async def test_invalid_theme_rejected(db_session):
    with pytest.raises(HTTPException) as exc:
        await admin_set_appearance(SiteAppearanceBody(theme="nope"), _admin=None, db=db_session)
    assert exc.value.status_code == 400


def test_admin_write_requires_admin():
    assert get_current_admin in [
        dep.call for route in app.routes
        if getattr(route, "path", "") == "/api/admin/settings/appearance"
        for dep in route.dependant.dependencies
    ]
