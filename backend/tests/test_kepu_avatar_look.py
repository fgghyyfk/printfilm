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
    DEFAULT_LOOK_STYLE,
    LOOK_STYLES,
    look_prompt,
    look_render,
    needs_scene,
    normalize_segments,
    scene_prompt,
    script_user_prompt,
    split_highlights,
)


def test_look_prompt_styles_and_beauty():
    """定妆照：写实保留本人，卡通画成角色；都带美颜瘦脸与对口型构图；未知风格回退默认（3D 卡通）。"""
    photo = look_prompt("lab")
    assert "SAME person" in photo and "lab coat" in photo and "photorealistic" in photo
    cartoon = look_prompt("cartoon3d")
    assert "cartoon character" in cartoon and "3D animated" in cartoon
    for p in (photo, cartoon, look_prompt("anime")):
        assert "slimmer" in p and "mouth gently closed" in p
    assert look_prompt("nope") == look_prompt(DEFAULT_LOOK_STYLE) == cartoon
    assert look_render("anime") == "anime" and look_render("") == "photo"


def test_scene_prompt_keeps_character_and_style():
    """场景图只换背景，人物与画风不变；空场景有兜底。"""
    p = scene_prompt("a seaside at sunset", "3d")
    assert "exactly the same" in p and "a seaside at sunset" in p and "3D animated" in p
    assert "photorealistic" in scene_prompt("x")
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


def test_original_look_keeps_outfit_and_removes_text():
    """保持原样：沿用原照片的衣服与场景，去掉字幕标题条，只轻度美颜，按胸像近景重新取景。"""
    from app.services.kepu_avatar_prompts import ORIGINAL_LOOK

    p = look_prompt(ORIGINAL_LOOK, "chest")
    assert "same outfit" in p and "title bars" in p and "light, natural beauty" in p
    assert "Chest-up medium close-up" in p and "one third" in p
    assert "one quarter" in look_prompt("casual", "waist")


def test_script_system_by_style_and_scene_mode():
    """观点口播要两行钩子标题；固定场景只要全片一个 scene，每段换场景要求段内 scene。"""
    from app.services.kepu_avatar_prompts import script_system

    opinion_fixed = script_system("opinion", "fixed")
    assert "title2" in opinion_fixed and "全片只用一个出镜场景" in opinion_fixed
    assert '"scene": "English background description", "segments"' in opinion_fixed
    kepu_seg = script_system("kepu", "per_segment")
    assert "儿童/大众科普" in kepu_seg and '"narration": "口播", "scene"' in kepu_seg


def test_normalize_uses_global_scene_and_shot_scene_rules():
    """段内没场景时用全片场景；「保持原样 + 固定场景」不再画场景图。"""
    from app.services.kepu_avatar_look import shot_scene

    segs = normalize_segments({"scene": "hotel lobby", "segments": [{"narration": "大家好，今天聊聊平台", "keywords": ["平台"]}]})
    assert segs[0]["scene"] == "hotel lobby"
    assert shot_scene("hotel lobby", scene_mode="fixed", look_style="original") == ""
    assert shot_scene("hotel lobby", scene_mode="fixed", look_style="casual") == "hotel lobby"
    assert shot_scene("sky", scene_mode="per_segment", look_style="original") == "sky"


def test_clean_topics():
    """批量选题去空去重，最多 50 个。"""
    from app.services.kepu_avatar_batch import MAX_BATCH, clean_topics

    assert clean_topics(["  选题一 ", "", "选题一", "x", "选题二"]) == ["选题一", "选题二"]
    assert len(clean_topics([f"选题{i}" for i in range(80)])) == MAX_BATCH
