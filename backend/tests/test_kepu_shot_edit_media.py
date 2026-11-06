"""科普分镜保存只在真实变化时作废素材；弹窗总是提交全部字段。"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import update_shot
from app.schemas import ShotUpdate
from app.services.seedance_segments import apply_segment_script_edit
from tests.conftest import make_user
from tests.test_kepu_shot_edit_demote import _make_done_project

# 真实系统里脚本总是规范化后才存储。
_NORMALIZED = apply_segment_script_edit("【0-4秒】\n【画面】工程师调试电路\n【旁白】原来的旁白", bgm_mood="")
SCRIPT = _NORMALIZED["segment_script"]


async def _project(db, monkeypatch, tmp_path, *, native):
    """已完成项目，并固定配音模式。"""
    user = await make_user(db)
    project, shot, sibling = await _make_done_project(db, user, tmp_path)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    monkeypatch.setattr("app.services.pipeline._kepu_seedance_native_audio", lambda _project: native)
    shot.segment_script = SCRIPT
    shot.video_prompt = "原视频提示词"
    shot.img_prompt = "原首帧"
    shot.narration = "原来的旁白"
    shot.duration = _NORMALIZED["duration"]
    shot.camera = "推镜"
    shot.image_url = "/static/a.png"
    shot.video_url = "/static/a.mp4"
    shot.last_frame_url = "/static/a_last.png"
    await db.flush()
    return user, project, shot, sibling


def _full_payload(shot, **changes):
    """模拟弹窗提交：未改动的字段也原样带上。"""
    return ShotUpdate(**{
        "narration": shot.narration, "overlay_title": shot.overlay_title,
        "overlay_subtitle": shot.overlay_subtitle, "img_prompt": shot.img_prompt,
        "video_prompt": shot.video_prompt, "segment_script": shot.segment_script,
        "duration": shot.duration, "camera": shot.camera, **changes,
    })


@pytest.mark.parametrize("native", [False, True])
async def test_unchanged_full_payload_keeps_all_media(db_session: AsyncSession, monkeypatch, tmp_path, native):
    """打开弹窗不改内容直接保存，图和视频都不能丢。"""
    user, project, shot, _ = await _project(db_session, monkeypatch, tmp_path, native=native)
    await update_shot(project.id, shot.id, _full_payload(shot), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.image_url == "/static/a.png" and shot.video_url == "/static/a.mp4"
    assert shot.segment_script == SCRIPT


async def test_title_edit_keeps_media(db_session: AsyncSession, monkeypatch, tmp_path):
    """只改后期叠字，不涉及任何生成输入。"""
    user, project, shot, _ = await _project(db_session, monkeypatch, tmp_path, native=True)
    await update_shot(project.id, shot.id, _full_payload(shot, overlay_title="新标题"), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.overlay_title == "新标题"
    assert shot.image_url == "/static/a.png" and shot.video_url == "/static/a.mp4"


async def test_narration_edit_external_audio_keeps_image_and_video(db_session: AsyncSession, monkeypatch, tmp_path):
    """外部配音：改旁白只需重配音，视频画面不受影响。"""
    user, project, shot, sibling = await _project(db_session, monkeypatch, tmp_path, native=False)
    await update_shot(project.id, shot.id, _full_payload(shot, narration="新的旁白"), db=db_session, user=user)
    await db_session.refresh(shot)
    await db_session.refresh(sibling)
    assert shot.image_url == "/static/a.png" and shot.video_url == "/static/a.mp4"
    assert shot.narration == "新的旁白" and "新的旁白" in shot.segment_script
    assert shot.audio_url is None and sibling.audio_url is None


async def test_narration_edit_native_audio_keeps_image_only(db_session: AsyncSession, monkeypatch, tmp_path):
    """内置口播：声音在视频里，视频必须重做，但首帧图不用重画。"""
    user, project, shot, sibling = await _project(db_session, monkeypatch, tmp_path, native=True)
    sibling_audio = sibling.audio_url
    await update_shot(project.id, shot.id, _full_payload(shot, narration="新的旁白"), db=db_session, user=user)
    await db_session.refresh(shot)
    await db_session.refresh(sibling)
    assert shot.image_url == "/static/a.png" and shot.video_url is None
    assert sibling.audio_url == sibling_audio


async def test_narration_only_payload_syncs_script(db_session: AsyncSession, monkeypatch, tmp_path):
    """只提交旁白时也同步脚本，否则重生视频仍朗读旧稿。"""
    user, project, shot, _ = await _project(db_session, monkeypatch, tmp_path, native=True)
    await update_shot(project.id, shot.id, ShotUpdate(narration="新的旁白"), db=db_session, user=user)
    await db_session.refresh(shot)
    assert "新的旁白" in shot.segment_script and "原来的旁白" not in shot.segment_script


async def test_first_frame_edit_clears_image_and_video(db_session: AsyncSession, monkeypatch, tmp_path):
    """首帧描述真实变化才作废图及其下游。"""
    user, project, shot, _ = await _project(db_session, monkeypatch, tmp_path, native=False)
    await update_shot(project.id, shot.id, _full_payload(shot, img_prompt="全新的首帧"), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.image_url is None and shot.video_url is None and shot.last_frame_url is None


@pytest.mark.parametrize("field,value", [
    ("segment_script", "【0-4秒】\n【画面】工程师改看示波器\n【旁白】原来的旁白\n【字幕】字幕"),
    ("duration", 7),
    ("camera", "拉镜"),
])
async def test_video_input_change_clears_video_keeps_image(db_session: AsyncSession, monkeypatch, tmp_path, field, value):
    """视频输入真实变化：视频重做，首帧图保留。"""
    user, project, shot, _ = await _project(db_session, monkeypatch, tmp_path, native=False)
    await update_shot(project.id, shot.id, _full_payload(shot, **{field: value}), db=db_session, user=user)
    await db_session.refresh(shot)
    assert shot.video_url is None and shot.image_url == "/static/a.png"
