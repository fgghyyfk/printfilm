"""Promptoll 视频：开启后在运行时路由追加只含视频的渠道与 Seedance 模型，走方舟任务协议。"""

from __future__ import annotations

import pytest

from app.schemas_routing import DefaultModels, LogicalModel, LogicalModelBinding, SystemModelChannel
from app.services import model_settings
from app.services import promptoll_video as pv
from app.services.media_models import MediaValidationError, get_strategy

ARK = "https://ark.cn-beijing.volces.com/api/v3"


def _ark_state():
    """线上现状：方舟渠道只配了文本与 Seedream 生图，没有视频模型。"""
    channel = SystemModelChannel(id="ark", name="方舟", base_url=ARK, api_key="ark-key", enabled=True)
    models = [LogicalModel(id="doubao-seedream-5-0-pro-260628", capability="image", strategy_id="seedream-5.0-pro",
                           bindings=[LogicalModelBinding(id="ark:image", channel_id="ark",
                                                         upstream_model="doubao-seedream-5-0-pro-260628")])]
    defaults = DefaultModels(image_model="doubao-seedream-5-0-pro-260628")
    return [channel], models, defaults


def _flat(**kw):
    return {"promptoll_video_enabled": True, "promptoll_api_key": "zai_test", "promptoll_video_default": "seedance-2.0",
            "model_video": "", "model_video_2": "", **kw}


def test_disabled_or_without_key_leaves_routing_untouched():
    channels, models, defaults = _ark_state()
    for flat in (_flat(promptoll_video_enabled=False), _flat(promptoll_api_key="")):
        out = pv.augment_routing(channels, models, defaults, flat)
        assert out[0] == channels and out[1] == models and out[2] == defaults


def test_enabled_appends_video_only_channel_last_and_sets_default():
    channels, models, defaults = _ark_state()
    ch, lm, df, flat = pv.augment_routing(channels, models, defaults, _flat())
    assert [c.id for c in ch] == ["ark", pv.PROMPTOLL_VIDEO_CHANNEL_ID]  # 主渠道仍是第一个生效渠道
    assert ch[-1].base_url == pv.PROMPTOLL_BASE_URL and ch[-1].api_key == "zai_test"
    video = {m.id: m for m in lm if m.capability == "video"}
    assert set(video) == {"seedance-2.0", "seedance-2.0-fast", "seedance-2.5"}
    assert video["seedance-2.0"].bindings[0].upstream_model == "dreamina-seedance-2-0-260128"
    assert df.video_model == "seedance-2.0" and df.image_model == defaults.image_model
    assert flat["model_video"] == "seedance-2.0" and flat["model_video_2"] == "seedance-2.0"


def test_disabled_model_is_marked_and_default_falls_back():
    channels, models, defaults = _ark_state()
    _, lm, df, _ = pv.augment_routing(channels, models, defaults, _flat(promptoll_video_default="seedance-2.5"),
                                      {"seedance-2.5"})
    assert next(m for m in lm if m.id == "seedance-2.5").enabled is False
    assert df.video_model == "seedance-2.0"


def test_main_channel_video_model_wins():
    channels, models, defaults = _ark_state()
    models = [*models, LogicalModel(id="seedance-2.0", capability="video", strategy_id="seedance-2.0",
                                    bindings=[LogicalModelBinding(id="ark:video", channel_id="ark",
                                                                  upstream_model="doubao-seedance-2-0-260128")])]
    defaults = defaults.model_copy(update={"video_model": "seedance-2.0"})
    _, lm, df, flat = pv.augment_routing(channels, models, defaults, _flat(model_video="seedance-2.0"))
    assert [m.bindings[0].channel_id for m in lm if m.id == "seedance-2.0"] == ["ark"]
    assert df.video_model == "seedance-2.0" and flat["model_video"] == "seedance-2.0"


def test_dreamina_ids_map_to_seedance_strategies():
    assert get_strategy("dreamina-seedance-2-0-260128").model_id == "seedance-2.0"
    assert get_strategy("dreamina-seedance-2-0-fast-260128").model_id == "seedance-2.0-fast"
    assert get_strategy("dreamina-seedance-2-5-260628").model_id == "seedance-2.5"
    with pytest.raises(MediaValidationError):
        get_strategy("dreamina-seedance-2-0-mini-260615")


def test_promptoll_keeps_ark_task_protocol():
    from app.services.tokenfree_video import prepare_video_create_body, remap_video_path

    path = "/contents/generations/tasks"
    assert remap_video_path(path, base_url=pv.PROMPTOLL_BASE_URL) == path
    assert remap_video_path(path + "/abc", base_url=pv.PROMPTOLL_BASE_URL + "/") == path + "/abc"
    body = {"model": "dreamina-seedance-2-0-260128", "content": [{"type": "text", "text": "x"}], "duration": 5}
    assert prepare_video_create_body(body, base_url=pv.PROMPTOLL_BASE_URL) == body
    # TokenFree 仍改写为 /videos
    assert remap_video_path(path, base_url="https://www.tokenfree.com/v1") != path


@pytest.fixture
def promptoll_snapshot(monkeypatch):
    channels, models, defaults = _ark_state()
    ch, lm, df, _ = pv.augment_routing(channels, models, defaults, _flat())
    saved = model_settings.get_routing_snapshot()
    model_settings._refresh_routing_snapshot(ch, lm, df)
    yield
    model_settings._refresh_routing_snapshot(saved.channels, saved.logical_models, saved.default_models,
                                             saved.media_reference_limits)


def test_pin_and_restore_route_through_promptoll(promptoll_snapshot):
    from app.services.media_models import runtime

    route = runtime.pin_route("video", "seedance-2.0")
    assert route["channel_id"] == pv.PROMPTOLL_VIDEO_CHANNEL_ID
    assert route["upstream_model"] == "dreamina-seedance-2-0-260128"
    assert route["base_url"] == pv.PROMPTOLL_BASE_URL and "api_key" not in route
    restored = runtime.restore_route(route)
    assert restored.api_key == "zai_test"


def test_catalog_lists_promptoll_seedance(promptoll_snapshot):
    from app.services.media_catalog import catalog_payload

    payload = catalog_payload()
    ids = [row["id"] for row in payload["video_models"]]
    assert "seedance-2.0" in ids and "seedance-2.5" in ids
    assert payload["defaults"]["video_model"] == "seedance-2.0"


def test_settings_fields_registered():
    from app.schemas_settings import AdminModelSettingsPatch, model_config_field_names

    assert {"promptoll_video_enabled", "promptoll_video_default"} <= set(model_config_field_names())
    with pytest.raises(ValueError):
        AdminModelSettingsPatch(promptoll_video_default="seedance-9")


def test_result_download_headers_only_for_promptoll_proxy(monkeypatch):
    base = pv.PROMPTOLL_BASE_URL + "/contents/generations/tasks/abc"
    assert pv.promptoll_media_headers(base + "/content", "zai_route") == {"Authorization": "Bearer zai_route"}
    assert pv.is_promptoll_result_url(base + "/last_frame")
    monkeypatch.setattr(pv, "promptoll_video_settings", lambda flat=None: (True, "zai_settings", "seedance-2.0"))
    assert pv.promptoll_media_headers(base + "/content") == {"Authorization": "Bearer zai_settings"}
    for url in (base, "https://cdn.example/x.mp4", "https://api.promptoll.com.evil/v1/contents/generations/tasks/a/content",
                "http://api.promptoll.com/v1/contents/generations/tasks/a/content"):
        assert pv.promptoll_media_headers(url, "zai_route") is None, url


async def test_download_result_media_sends_promptoll_key(monkeypatch, tmp_path):
    from app.services import ark as ark_mod
    from app.services import storage

    seen = {}

    async def fake_download(url, dest, headers=None, timeout=None, retries=1):
        seen.update(url=url, headers=headers)

    monkeypatch.setattr(storage, "download_to", fake_download)
    monkeypatch.setattr(pv, "promptoll_video_settings", lambda flat=None: (True, "zai_settings", "seedance-2.0"))
    gw = ark_mod.ArkGateway()
    url = pv.PROMPTOLL_BASE_URL + "/contents/generations/tasks/t1/content"
    await gw.download_result_media(url, tmp_path / "v.mp4")
    assert seen == {"url": url, "headers": {"Authorization": "Bearer zai_settings"}}
    await gw.download_result_media("https://cdn.example/v.mp4", tmp_path / "w.mp4")
    assert seen["headers"] is None
