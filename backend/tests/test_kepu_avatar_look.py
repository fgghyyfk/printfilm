"""数字人「只取人脸」：提示词、文案解析、重点文字时间窗、场景判定、改图响应解析的纯单测。"""
from __future__ import annotations

import base64
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services import avatar_ai
from app.services.ffmpeg_compose import _highlight_drawtext, build_video_caption_vf, highlight_windows
from app.services.kepu_avatar_look import (
    LOOK_STYLES,
    look_prompt,
    needs_scene,
    normalize_segments,
    scene_prompt,
    script_user_prompt,
    split_highlights,
)


def test_look_prompt_keeps_face_and_changes_outfit():
    """定妆照只保留脸，服装按风格替换；未知风格回退默认。"""
    p = look_prompt("lab")
    assert "SAME person" in p and "lab coat" in p and "mouth gently closed" in p
    assert look_prompt("nope") == look_prompt("casual")
    assert set(LOOK_STYLES) >= {"casual", "school", "lab", "host", "outdoor"}


def test_scene_prompt_keeps_person():
    """场景图只换背景，人物不变；空场景有兜底。"""
    p = scene_prompt("a seaside at sunset")
    assert "exactly the same" in p and "a seaside at sunset" in p
    assert "educational studio" in scene_prompt("")


def test_split_highlights():
    """重点词去空去重、截 8 字、最多 3 个，兼容多种分隔符。"""
    assert split_highlights("散射|波长短｜散射、蓝光,晚霞") == ["散射", "波长短", "蓝光"]
    assert split_highlights(["一二三四五六七八九"]) == ["一二三四五六七八"]
    assert split_highlights(None) == []


def test_parse_json_reply_tolerates_fence():
    """模型回复带 ```json 包裹或前后说明时仍能解析。"""
    assert avatar_ai.parse_json_reply('好的：\n```json\n{"a": 1}\n```') == {"a": 1}
    assert avatar_ai.parse_json_reply('前言 {"segments": []} 结尾') == {"segments": []}
    with pytest.raises(ValueError):
        avatar_ai.parse_json_reply("没有 JSON")


def test_normalize_segments_filters_keywords():
    """丢掉无口播段；重点词优先保留口播里出现过的。"""
    segs = normalize_segments({"segments": [
        {"title": "散射", "narration": "蓝光最容易被撞散，这叫散射。", "scene": "sky", "keywords": ["散射", "不存在"]},
        {"title": "空", "narration": "  "},
    ]})
    assert len(segs) == 1 and segs[0]["keywords"] == ["散射"]
    with pytest.raises(RuntimeError):
        normalize_segments({"segments": []})


def test_script_prompt_modes():
    """口播稿模式要求原文不改；主题模式给段数。"""
    assert "一字不改" in script_user_prompt("原稿", "script", (3, 5))
    assert "4~6" in script_user_prompt("为什么天是蓝的", "theme", (4, 6))


def test_highlight_windows_follow_narration():
    """重点词按在口播中的位置依次出现，不重叠，每个至少约 1.8 秒。"""
    text = "阳光里有七种颜色，其中蓝光波长短，最容易发生散射。"
    wins = highlight_windows(["散射", "七种颜色"], text, 8.0)
    assert [w for w, _, _ in wins] == ["七种颜色", "散射"]
    for (_, a1, b1), (_, a2, _) in zip(wins, wins[1:]):
        assert b1 <= a2 + 1e-6
    assert all(b - a >= 0.6 for _, a, b in wins)
    assert highlight_windows([], text, 8.0) == []


def test_highlight_only_in_band_layout():
    """贴纸只在数字人 band 布局里画。"""
    band = build_video_caption_vf(narration="这叫散射", duration=4, w=720, h=1280, font=None,
                                  subtitle_layout="band", highlights=["散射"])
    top = build_video_caption_vf(narration="这叫散射", duration=4, w=720, h=1280, font=None,
                                 subtitle_layout="top", highlights=["散射"])
    assert "0xFFD400@0.95" in band and "0xFFD400@0.95" not in top
    assert "alpha=" in _highlight_drawtext(["散射"], "这叫散射", 4, 720, 1280, None)


def test_needs_scene(tmp_path, monkeypatch):
    """有场景描述且仍是定妆照占位（或文件丢失）才需要画；没描述不画。"""
    look = "/static/generated/p1/avatar_look_1.png"
    existing = tmp_path / "scene.png"
    existing.write_bytes(b"x")
    monkeypatch.setattr("app.services.kepu_avatar_look.storage.local_path_from_url",
                        lambda url: existing if url.endswith("scene_ok.png") else tmp_path / "missing.png")
    shot = lambda **kw: SimpleNamespace(**{"img_prompt": "sky", "image_url": look, **kw})  # noqa: E731
    assert needs_scene(shot(), look)
    assert not needs_scene(shot(img_prompt=" "), look)
    assert not needs_scene(shot(image_url="/static/generated/p1/scene_ok.png"), look)
    assert needs_scene(shot(image_url="/static/generated/p1/scene_gone.png"), look)


def test_resolve_image_model(monkeypatch):
    """项目模型优先，其次后台默认。"""
    monkeypatch.setattr(avatar_ai, "get_settings", lambda: SimpleNamespace(avatar_image_model="gpt-image-2"))
    assert avatar_ai.resolve_image_model("qwen-image-edit-plus") == "qwen-image-edit-plus"
    assert avatar_ai.resolve_image_model("") == "gpt-image-2"


async def test_image_bytes_from_b64():
    """改图响应 b64_json / data URL 都能取到图片字节。"""
    raw = b"\x89PNG fake"
    b64 = base64.b64encode(raw).decode()
    assert await avatar_ai._image_bytes_from_response({"data": [{"b64_json": b64}]}, None) == raw
    got = await avatar_ai._image_bytes_from_response({"data": [{"url": f"data:image/png;base64,{b64}"}]}, None)
    assert got == raw
    with pytest.raises(RuntimeError):
        await avatar_ai._image_bytes_from_response({"data": []}, None)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_highlight_filter_runs_in_ffmpeg(tmp_path):
    """贴纸滤镜能被 ffmpeg 正确解析（转义无误）。"""
    vf = _highlight_drawtext(["散射", "波长短"], "蓝光波长短，最容易发生散射", 3.0, 360, 640, None)
    out = tmp_path / "o.mp4"
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=360x640:d=3",
                           "-vf", vf, str(out)], capture_output=True, text=True)
    if "No such filter: 'drawtext'" in proc.stderr:
        pytest.skip("本机 ffmpeg 不带 drawtext")
    assert proc.returncode == 0, proc.stderr
