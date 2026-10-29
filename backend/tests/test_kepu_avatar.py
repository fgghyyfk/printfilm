"""数字人口播（pipeline_mode=avatar）的纯单测：阶段判定、段数估算、标题条、计费估算、模拟上游。"""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services import avatar_video
from app.services.ffmpeg_compose import _band_title_drawtext, _overlay_drawtext, _scale_pad, probe_duration
from app.services.kepu_stages import is_avatar, project_audio_ready, resolve_kepu_billing_phase
from app.services.seedance_segments import suggested_avatar_shot_range


def _shot(**kw):
    """构造最小分镜桩。"""
    base = dict(image_url="/static/generated/p1/avatar_portrait_1.png", image_ark_url=None, audio_url=None,
                video_url=None, video_skip_reason=None, duration=12.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_avatar_shot_range_scales_with_text():
    """段数随口播字数增长，且不超过 12 段。"""
    assert suggested_avatar_shot_range("字" * 50) == (2, 3)
    lo, hi = suggested_avatar_shot_range("字" * 800)
    assert 8 <= lo <= hi <= 12
    assert suggested_avatar_shot_range("字" * 5000) == (12, 12)


def test_avatar_audio_requires_every_segment(monkeypatch):
    """数字人要求每段都有自己的配音；不认 full 模式的视频内置口播。"""
    monkeypatch.setattr("app.services.kepu_stages.shot_audio_file_ok", lambda s: bool(s.audio_url))
    project = SimpleNamespace(id=1, pipeline_mode="avatar", shots=[_shot(audio_url="/a.mp3"), _shot()])
    assert is_avatar(project)
    assert not project_audio_ready(project)
    assert resolve_kepu_billing_phase(project) == "assets"
    project.shots[1].audio_url = "/b.mp3"
    assert project_audio_ready(project)
    assert resolve_kepu_billing_phase(project) == "videos"
    for s in project.shots:
        s.video_url = "/v.mp4"
    assert resolve_kepu_billing_phase(project) == "compose"


def test_band_title_layout_yellow_then_white():
    """标题条：半透明底 + 黄色第一行 + 白色第二行，位于画面中下部。"""
    vf = _band_title_drawtext(720, 1280, "喝水的三个误区", "早上第一杯", None)
    assert "drawbox=x=0:y=896" in vf
    assert vf.index("0xFFD400") < vf.index("fontcolor=white:")
    # AI 生成标识 + 一行标题
    assert _overlay_drawtext(720, 1280, "标题", "", None, layout="band").count("drawtext") == 2
    # 没有标题时仍保留 AI 生成标识
    assert "AI 生成" in _band_title_drawtext(720, 1280, "", "", None)


def test_cover_fit_crops_instead_of_padding():
    """数字人视频裁满画布，不加黑边。"""
    assert "crop=720:1280" in _scale_pad(720, 1280, "cover")
    assert "pad=" in _scale_pad(720, 1280)


def test_avatar_video_estimate(monkeypatch):
    """视频阶段按未完成段秒数 × 单价预扣；单价 0 时为 0。"""
    from app.services import kepu_avatar

    project = SimpleNamespace(pipeline_mode="avatar", shots=[_shot(duration=10.0), _shot(duration=8.0, video_url="/v")])
    monkeypatch.setattr(kepu_avatar, "get_settings", lambda: SimpleNamespace(avatar_price_fen_per_sec=0))
    assert kepu_avatar.estimate_videos_fen(project) == 0
    monkeypatch.setattr(kepu_avatar, "get_settings", lambda: SimpleNamespace(avatar_price_fen_per_sec=3))
    assert kepu_avatar.estimate_videos_fen(project) == 30


def test_mock_only_without_service_url(monkeypatch):
    """配了服务地址时即使 ARK_MOCK 也走真服务。"""
    monkeypatch.setattr(avatar_video, "get_settings", lambda: SimpleNamespace(ark_mock=True, avatar_base_url=""))
    assert avatar_video.is_mock()
    monkeypatch.setattr(avatar_video, "get_settings",
                        lambda: SimpleNamespace(ark_mock=True, avatar_base_url="http://gpu:8780"))
    assert not avatar_video.is_mock()


def test_resolve_engine_defaults(monkeypatch):
    """项目引擎优先，非法值回退 infinitetalk。"""
    monkeypatch.setattr(avatar_video, "get_settings", lambda: SimpleNamespace(avatar_engine="longcat"))
    assert avatar_video.resolve_engine(None) == "longcat"
    assert avatar_video.resolve_engine("InfiniteTalk") == "infinitetalk"
    assert avatar_video.resolve_engine("unknown") == "infinitetalk"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
async def test_mock_round_trip(tmp_path, monkeypatch):
    """模拟模式：提交即成功，下载得到照片 + 音频的静态视频，时长跟音频一致。"""
    monkeypatch.setattr(avatar_video, "get_settings", lambda: SimpleNamespace(ark_mock=True, avatar_base_url=""))
    img, wav = tmp_path / "p.png", tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=360x480", "-frames:v", "1",
                    str(img)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", str(wav)], check=True)
    jid = await avatar_video.submit(img, wav, engine="infinitetalk")
    assert jid.startswith(avatar_video.MOCK_PREFIX)
    st = await avatar_video.fetch_once(jid)
    assert st.status == "succeeded"
    out = await avatar_video.download(jid, tmp_path / "o.mp4", image=img, audio=wav)
    assert abs((probe_duration(out) or 0) - 2.0) < 0.3
