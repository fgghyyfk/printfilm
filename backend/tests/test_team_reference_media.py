"""离线校验团队视频任务只读取其他内容，不越过分集/资产的写入范围。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect

from app.models import User
from app.models_drama import DramaAsset, DramaEpisodeFragment, DramaProject
from app.services import storage
from app.services.drama import asset_video, generation


def reference_asset(**overrides):
    """创建带嵌套音色绑定的参考资产，方便检测原对象被隐式修改。"""
    values = dict(id=2, project_id=17, name="角色", type="character", asset_type="image",
                  cover="/static/cover.png", url="/static/cover.png",
                  params={"voiceAudio": {"url": "/static/voice.mp3"},
                          "canvas": {"voiceAudio": {"url": "/static/voice.mp3"}}})
    return DramaAsset(**{**values, **overrides})


@pytest.mark.asyncio
async def test_read_only_reference_publication_uses_independent_snapshots(monkeypatch):
    """团队请求可转换公网引用，但不改变 ORM 资产或提交任何事务。"""
    asset = reference_asset()
    original = deepcopy(asset.params)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), get=AsyncMock())
    monkeypatch.setattr(storage, "republish_url", lambda url, **_: "https://media.example.com/" + url.rsplit("/", 1)[-1])
    result = await generation.ensure_reference_assets_public_urls(db, [asset], read_only=True)
    assert result[0] is not asset
    assert inspect(result[0]).session is None and inspect(result[0]).transient
    assert result[0].cover == "https://media.example.com/cover.png"
    assert result[0].params["voiceAudio"]["url"] == "https://media.example.com/voice.mp3"
    result[0].params["canvas"]["voiceAudio"]["extra"] = True
    assert asset.params == original
    assert asset.cover == "/static/cover.png" and asset.url == "/static/cover.png"
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_reference_publication_preserves_existing_cache(monkeypatch):
    """个人项目继续缓存公网 URL，避免改变既有生成流程。"""
    asset = reference_asset(params={})
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), get=AsyncMock())
    monkeypatch.setattr(storage, "republish_url", lambda _url, **_: "https://media.example.com/a.png")
    result = await generation.ensure_reference_assets_public_urls(db, [asset])
    assert result[0] is asset and asset.url == "https://media.example.com/a.png"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(asset)


@pytest.mark.asyncio
@pytest.mark.parametrize("narrator", [False, True])
@pytest.mark.parametrize("duration", [None, 1.1, 30.5])
async def test_team_invalid_audio_is_rejected_without_repair(monkeypatch, tmp_path, narrator, duration):
    """不可读、过短和过长均不能自动合成其他音色或重写角色/旁白绑定。"""
    asset = reference_asset()
    params = {"narrationVoiceAudio": {"url": "/static/narrator.mp3", "sourceAssetId": 9}} if narrator else {}
    project = DramaProject(id=17, team_id=8, params=params)
    original = deepcopy((asset.params, project.params))
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), get=AsyncMock())
    monkeypatch.setattr(storage, "project_dir", lambda _id: tmp_path)
    monkeypatch.setattr(generation, "probe_voice_url_duration", AsyncMock(return_value=duration))
    synthesize = AsyncMock()
    monkeypatch.setattr(generation, "synthesize_voice_asset", synthesize)
    with pytest.raises(RuntimeError, match="资产|旁白"):
        await generation.ensure_fragment_reference_audios(db, User(id=1), project,
                DramaEpisodeFragment(id=3), [] if narrator else [asset])
    assert (asset.params, project.params) == original
    synthesize.assert_not_awaited()
    db.get.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_ready_audio_only_probes_and_returns_original_refs(monkeypatch, tmp_path):
    """合格音频不触发生成、补足、数据库查询或写入。"""
    asset = reference_asset()
    project = DramaProject(id=17, team_id=8, params={"narrationVoiceAudio": {"url": "/static/voice.mp3"}})
    db = SimpleNamespace(commit=AsyncMock(), get=AsyncMock())
    probe = AsyncMock(return_value=2.1)
    monkeypatch.setattr(storage, "project_dir", lambda _id: tmp_path)
    monkeypatch.setattr(generation, "probe_voice_url_duration", probe)
    result = await generation.ensure_fragment_reference_audios(db, User(id=1), project,
                DramaEpisodeFragment(id=3), [asset])
    assert result == [asset] and result[0] is asset
    db.get.assert_not_awaited()
    db.commit.assert_not_awaited()
    probe.assert_awaited_once()
    assert "3" in str(probe.call_args.kwargs["work_dir"])


@pytest.mark.asyncio
async def test_personal_short_audio_still_repairs_binding(monkeypatch, tmp_path):
    """个人分镜继续保留自动补足逻辑。"""
    asset = reference_asset()
    project = DramaProject(id=17, team_id=None, params={})
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), get=AsyncMock())
    monkeypatch.setattr(storage, "project_dir", lambda _id: tmp_path)
    monkeypatch.setattr(storage, "local_path_from_url", lambda _url: tmp_path / "voice.mp3")
    monkeypatch.setattr(storage, "rel_static_url", lambda _path: "/static/voice.mp3")
    monkeypatch.setattr(generation, "probe_voice_url_duration", AsyncMock(return_value=1.1))
    monkeypatch.setattr(generation, "finalize_voice_reference_url", lambda *_args, **_kwargs: "/static/padded.mp3")
    await generation.ensure_fragment_reference_audios(db, User(id=1), project,
                DramaEpisodeFragment(id=3), [asset])
    assert asset.params["voiceAudio"]["url"] == "/static/padded.mp3"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_continuity_does_not_write_source_fragment(monkeypatch, tmp_path):
    """尾帧抽取可提供本次参考，不能改变来源分镜的 JSON。"""
    video = tmp_path / "source.mp4"
    video.write_bytes(b"test")
    fragment = DramaEpisodeFragment(id=5, episode_id=2, video=str(video), params={"generation": {"status": "done"}})
    original = deepcopy(fragment.params)
    monkeypatch.setattr(storage, "project_dir", lambda _id: tmp_path)
    monkeypatch.setattr(storage, "local_path_from_url", lambda _url: video)
    monkeypatch.setattr(storage, "rel_static_url", lambda _path: "/static/last.jpg")
    monkeypatch.setattr(storage, "republish_url", lambda url, **_: url)
    monkeypatch.setattr("app.services.ffmpeg_compose.extract_video_last_frame", lambda *_args: True)
    result = await generation.ensure_fragment_last_frame_url(DramaProject(id=17, team_id=8), fragment)
    assert result == "/static/last.jpg" and fragment.params == original


class StopPreparation(Exception):
    """在请求引用转换入口停止，以离线验证两个业务调用方的只读选择。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [None, 8])
@pytest.mark.parametrize("caller", ["fragment", "asset"])
async def test_video_preparation_selects_read_only_team_references(monkeypatch, team_id, caller):
    """分镜及资产视频调用同一只读策略，避免遗漏另一条写入路径。"""
    project = DramaProject(id=17, team_id=team_id, params={})
    asset = reference_asset()
    publish = AsyncMock(side_effect=StopPreparation)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(params={})),
                         execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))))
    if caller == "fragment":
        monkeypatch.setattr(generation, "ensure_fragment_reference_images", AsyncMock(return_value=[asset]))
        monkeypatch.setattr(generation, "ensure_reference_assets_public_urls", publish)
        execute = generation.prepare_fragment_video_for_submit(db, User(id=1), project,
                DramaEpisodeFragment(id=3, episode_id=2, sort_order=0, content="内容", duration_sec=8))
    else:
        monkeypatch.setattr(asset_video, "load_reference_assets", AsyncMock(return_value=[asset]))
        monkeypatch.setattr(asset_video, "ensure_reference_assets_public_urls", publish)
        execute = asset_video.generate_asset_video(db, User(id=1), project, asset, "画面")
    with pytest.raises(StopPreparation):
        await execute
    assert publish.call_args.kwargs["read_only"] is (team_id is not None)
