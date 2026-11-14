"""Model switches persist across settings saves and cache reloads."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import config
from app.models_settings import AppSettings, SystemModelChannelRow
from app.schemas_routing import AdminRoutingSettingsPatch, default_models_to_dict
from app.services import model_settings
from app.services.media_model_presets import all_preset_channel_models
from app.services.tokenfree_gateway import locked_tokenfree_channel



@pytest.fixture
async def routing_db():
    previous_snapshot = model_settings.get_routing_snapshot()
    previous_overlay = model_settings.get_overlay_dict()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AppSettings.__table__.create)
        await connection.run_sync(SystemModelChannelRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    channel = locked_tokenfree_channel(api_key="test-key", models=all_preset_channel_models(), enabled=True)
    models, defaults = model_settings._bootstrap_logical_from_channels([channel])
    async with sessions() as db:
        db.add(SystemModelChannelRow(id=channel.id, name=channel.name, base_url=channel.base_url,
                                    api_key_ciphertext=model_settings._encrypt_secret(channel.api_key),
                                    models=channel.models, enabled=True, protocol=channel.protocol))
        db.add(AppSettings(id="default", config_json={"flat": {},
            "logical_models": [model.model_dump() for model in models],
            "default_models": default_models_to_dict(defaults)}))
        await db.commit()
        try:
            yield db
        finally:
            model_settings._routing_snapshot = previous_snapshot
            model_settings._refresh_overlay({"flat": previous_overlay})
            config.reload_settings()
    await engine.dispose()


@pytest.mark.parametrize("capability", ["text", "image", "video", "audio"])
async def test_all_models_of_a_capability_can_be_disabled_and_reenabled(routing_db, capability):
    before = await model_settings.get_admin_routing_settings(routing_db)
    models = [model.model_copy(update={"enabled": False}) if model.capability == capability else model
              for model in before.logical_models]
    key = f"{capability}_model"
    assert any(model.capability == capability for model in models)
    saved, _ = await model_settings.patch_admin_routing_settings(routing_db,
        AdminRoutingSettingsPatch(logical_models=models, default_models=before.default_models))
    assert getattr(saved.default_models, key) == ""
    assert all(not model.enabled for model in saved.logical_models if model.capability == capability)
    # Reload and a subsequent ordinary save must not resurrect a preset/default.
    await model_settings.load_model_settings_cache(routing_db)
    saved, _ = await model_settings.patch_admin_routing_settings(routing_db, AdminRoutingSettingsPatch())
    assert getattr(saved.default_models, key) == ""
    assert all(not model.enabled for model in saved.logical_models if model.capability == capability)
    original = next(model for model in before.logical_models if model.capability == capability)
    models = [model.model_copy(update={"enabled": True}) if model.id == original.id else model
              for model in saved.logical_models]
    restored, _ = await model_settings.patch_admin_routing_settings(routing_db, AdminRoutingSettingsPatch(logical_models=models))
    assert getattr(restored.default_models, key) == original.id
    assert next(model for model in restored.logical_models if model.id == original.id).enabled


async def test_disabling_default_selects_an_enabled_model_and_preserves_its_bindings(routing_db):
    before = await model_settings.get_admin_routing_settings(routing_db)
    previous = before.default_models.image_model
    models = [model.model_copy(update={"enabled": False}) if model.id == previous else model
              for model in before.logical_models]
    saved, _ = await model_settings.patch_admin_routing_settings(routing_db,
        AdminRoutingSettingsPatch(logical_models=models, default_models=before.default_models))
    assert saved.default_models.image_model and saved.default_models.image_model != previous
    disabled = next(model for model in saved.logical_models if model.id == previous)
    assert not disabled.enabled
    assert disabled.bindings == next(model for model in before.logical_models if model.id == previous).bindings
    await model_settings.load_model_settings_cache(routing_db)
    after = await model_settings.get_admin_routing_settings(routing_db)
    assert after.default_models.image_model == saved.default_models.image_model
    assert not next(model for model in after.logical_models if model.id == previous).enabled


async def test_enabled_standard_image_default_is_not_changed_to_pro(routing_db):
    before = await model_settings.get_admin_routing_settings(routing_db)
    defaults = before.default_models.model_copy(update={"image_model": "seedream-5.0"})
    saved, _ = await model_settings.patch_admin_routing_settings(routing_db, AdminRoutingSettingsPatch(default_models=defaults))
    assert saved.default_models.image_model == "seedream-5.0"
    await model_settings.load_model_settings_cache(routing_db)
    after = await model_settings.get_admin_routing_settings(routing_db)
    assert after.default_models.image_model == "seedream-5.0"


async def test_disabled_default_falls_back_in_preset_order(routing_db):
    """停用默认生图模型后按推荐顺序取下一个（而不是按字母顺序），重载后保持。"""
    from app.services.media_model_presets import preset_ids

    before = await model_settings.get_admin_routing_settings(routing_db)
    previous = before.default_models.image_model
    models = [model.model_copy(update={"enabled": False}) if model.id == previous else model
              for model in before.logical_models]
    saved, _ = await model_settings.patch_admin_routing_settings(routing_db, AdminRoutingSettingsPatch(logical_models=models))
    expected = next(mid for mid in preset_ids("image") if mid != previous)
    assert saved.default_models.image_model == expected
    await model_settings.load_model_settings_cache(routing_db)
    after = await model_settings.get_admin_routing_settings(routing_db)
    assert after.default_models.image_model == expected
