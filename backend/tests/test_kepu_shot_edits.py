"""分镜制作字段真实变化判定的纯单测，不依赖数据库。"""
import pytest

from app.models import Shot
from app.services.kepu_shot_edits import shot_video_content_changed


@pytest.mark.parametrize("key", ["video_prompt", "segment_script"])
@pytest.mark.parametrize("change,expected", [
    ("same", False), ("narration", False), ("subtitle", False),
    ("action", True), ("timeline", True),
])
def test_video_prompt_change_ignores_only_voice_and_subtitle(key, change, expected) -> None:
    """忽略旁白内容和后期字幕 cue，保留画面动作及时间轴变化。"""
    script = "【字幕：后期叠字】\n@duration:4\n主体向前走\n@duration:4\n【旁白】旧旁白"
    shot = Shot(video_prompt=script, segment_script=script if key == "segment_script" else "", duration=8, camera="slow pan")
    replacement = {
        "same": script,
        "narration": script.replace("旧旁白", "新旁白"),
        "subtitle": script.replace("后期叠字", "底部字幕"),
        "action": script.replace("主体向前走", "主体转身"),
        "timeline": script.replace("@duration:4", "@duration:3", 1),
    }[change]

    assert shot_video_content_changed(shot, {key: replacement}) is expected


@pytest.mark.parametrize("change,expected", [
    ("promote", False), ("narration", False), ("action", True),
    ("timeline", True), ("fallback", True), ("unused", False),
])
def test_effective_script_changes_follow_pipeline_fallback(change, expected) -> None:
    """按 segment_script or video_prompt 判定实际使用的画面，兼容历史脚本回填。"""
    script = "@duration:4\n主体向前走\n@duration:4\n【旁白】旧旁白"
    shot = Shot(video_prompt=script, segment_script="", duration=8, camera="slow pan")
    data = {"segment_script": script, "video_prompt": script}
    if change in {"narration", "action", "timeline"}:
        old, new = {"narration": ("旧旁白", "新旁白"), "action": ("主体向前走", "主体转身"), "timeline": ("@duration:4", "@duration:3")}[change]
        data = {key: value.replace(old, new) for key, value in data.items()}
    elif change == "fallback":
        shot.segment_script = script.replace("主体向前走", "主体转身")
        data = {"segment_script": ""}
    elif change == "unused":
        shot.segment_script = script
        data = {"video_prompt": script.replace("主体向前走", "主体转身")}

    assert shot_video_content_changed(shot, data) is expected


@pytest.mark.parametrize("data,expected", [
    ({}, False), ({"narration": "新旁白"}, False),
    ({"overlay_subtitle": "新字幕"}, False), ({"duration": 8.0}, False),
    ({"camera": " slow pan "}, False), ({"duration": 6.0}, True),
    ({"camera": "zoom in"}, True),
])
def test_video_duration_and_camera_require_actual_changes(data, expected) -> None:
    """时长按数值、机位按去除首尾空白后的文本比较。"""
    shot = Shot(duration=8, camera="slow pan")

    assert shot_video_content_changed(shot, data) is expected
