"""Real media acceptance for draining persisted legacy production stages.

All provider calls are local substitutes. The shared DB fixture uses a separate
test database and rollback; generated files live only inside pytest's tmp_path.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Project, ProjectStatus, Template
from app.models_creation import CreationMessage, CreationRun, CreationSession
from app.models_tasks import TaskRun
from app.schemas_creation import CreationDecision
from app.services import creation, ffmpeg_compose, pipeline, storage
from app.services.ark import ShotPlan, StoryboardResult
from app.services.tasks import executor
from app.services.templates_seed import TEMPLATES
from tests.conftest import make_user
from tests.test_creation_workflow import persisted_turn


def _media_command(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr[-3000:]
    return result


@pytest.mark.asyncio
async def test_persisted_creation_delivers_real_vertical_mp4_with_voice_motion_and_captions(
    db_session, monkeypatch, tmp_path,
):
    ffmpeg = shutil.which(get_settings().ffmpeg_path) or shutil.which("ffmpeg")
    ffprobe = shutil.which(get_settings().ffprobe_path) or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("real FFmpeg acceptance requires ffmpeg and ffprobe")
    if not ffmpeg_compose._find_cjk_font():
        pytest.skip("real Chinese-caption acceptance requires a CJK font")

    db = db_session
    settings = get_settings()
    monkeypatch.setattr(settings, "ark_mock", True)
    monkeypatch.setattr(settings, "billing_enabled", False)
    monkeypatch.setattr(settings, "ffmpeg_path", ffmpeg)
    monkeypatch.setattr(settings, "ffprobe_path", ffprobe)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path / "static")
    monkeypatch.setattr(storage, "GENERATED_ROOT", tmp_path / "static" / "generated")
    monkeypatch.setattr("app.services.oss.oss_enabled", lambda: False)
    # No model, storage, music, or other HTTP service is permitted in this test.
    network = AsyncMock(side_effect=AssertionError("media acceptance must stay offline"))
    monkeypatch.setattr(httpx.AsyncClient, "send", network)
    monkeypatch.setattr(pipeline, "resolve_bgm_path", lambda _mood: None)
    monkeypatch.setattr(pipeline, "_record_usage_est", AsyncMock())
    monkeypatch.setattr(pipeline, "_record_seedream_usage", AsyncMock())

    @asynccontextmanager
    async def local_session():
        yield db

    for module in (creation, pipeline, executor):
        monkeypatch.setattr(module, "AsyncSessionLocal", local_session)

    template = await db.get(Template, "anim_3d")
    if template is None:
        template = Template(**next(item for item in TEMPLATES if item["id"] == "anim_3d"))
        db.add(template)
        await db.flush()
    user = await make_user(db)

    narration = ["阳光遇到空气会向四周散射。", "蓝光散射得更多，让天空呈现蓝色。"]
    copy_text = "".join(narration)
    monkeypatch.setattr(creation, "_mock_decision", lambda _history, _brief: CreationDecision(
        action="produce", title="为什么天空是蓝色的", copy_text=copy_text,
        reply="文案已经整理好，继续制作。", brief={"target_duration_sec": 60, "output_ratio": "9:16"},
    ))
    images_ready = asyncio.Event()
    progress_events = []

    async def progress(_project_id, payload):
        progress_events.append(payload)
        if payload.get("message", "").startswith("出图 2/2"):
            images_ready.set()

    monkeypatch.setattr(pipeline, "publish_progress", progress)
    # A single transaction/session is deliberately reused by the fixture. These
    # cancellation reads are covered separately; avoid concurrent use of its
    # connection by the image/audio branches of this successful-path test.
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock())

    async def storyboard(**kwargs):
        assert kwargs["source_type"] == "script"
        assert kwargs["source_text"] == copy_text
        assert kwargs["pipeline_mode"] == "image_text"
        assert kwargs["output_ratio"] == "9:16"
        assert "3D" in kwargs["style_prefix"] or "三维" in kwargs["style_prefix"]
        return StoryboardResult(shots=[
            ShotPlan(shot=index, duration=4, text=text, img_prompt="3D 动画小角色观察天空",
                     video_prompt="缓慢推近", camera="push-in", bgm="",
                     overlay_title="蓝色天空", overlay_subtitle="观察阳光")
            for index, text in enumerate(narration, 1)
        ], character_bible="圆润的三维卡通角色")

    image_calls = []

    async def image(prompt, _negative, _refs, **kwargs):
        assert kwargs["aspect_ratio"] == "9:16"
        assert "三维" in prompt or "3D" in prompt
        image_calls.append(kwargs["shot_no"])
        path = storage.project_dir(kwargs["project_id"]) / f"mock_{kwargs['shot_no']}.png"
        _media_command([
            ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=size=256x256:rate=1",
            "-frames:v", "1", str(path),
        ])
        url = storage.publish_local(path)
        return SimpleNamespace(local_url=url, remote_url=url)

    audio_calls = []

    async def tts(text, voice, **kwargs):
        assert text == copy_text
        assert voice
        audio_calls.append(text)
        # Let image writes finish before this branch writes through the single
        # transaction-bound fixture session; actual FFmpeg remains unchanged.
        await asyncio.wait_for(images_ready.wait(), timeout=10)
        path = storage.project_dir(kwargs["project_id"]) / "mock_tts.mp3"
        _media_command([
            ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=3",
            "-c:a", "libmp3lame", "-q:a", "3", str(path),
        ])
        return storage.publish_local(path)

    video_model = AsyncMock(side_effect=AssertionError("image_text must not call a video model"))
    gateway = SimpleNamespace(
        chat_storyboard=storyboard, gen_image=image, tts=tts, gen_video_i2v=video_model,
        _sanitize_seedream_prompt=lambda value: value,
    )
    monkeypatch.setattr(pipeline, "get_ark", lambda: gateway)
    ffmpeg_commands = []
    original_run = ffmpeg_compose._run

    def traced_ffmpeg(command):
        ffmpeg_commands.append(command.copy())
        original_run(command)

    monkeypatch.setattr(ffmpeg_compose, "_run", traced_ffmpeg)

    session = CreationSession(user_id=user.id, template_id="anim_3d", brief={})
    db.add(session)
    await db.flush()
    await persisted_turn(db, user, session, "给小学生讲讲为什么天空是蓝色的")
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    executed_stages = []
    for _ in range(4):
        run = await db.get(CreationRun, session.active_run_id) if session.active_run_id else None
        task = await db.get(TaskRun, run.current_task_id if run else session.active_turn_task_id)
        # Direct text stages are marked running when committed; conftest stops
        # their background launch so this loop can execute every stage itself.
        assert task.status in {"pending", "running"}, (task.status, task.error_message)
        executed_stages.append(task.payload.get("phase", task.task_type))
        await executor.execute_task_run(task.id)
        await db.refresh(task)
        assert task.status == "succeeded", task.error_message
        # Completion callbacks, rather than a UI next-step action, create the
        # next task. The shorter TTS above retimes the two planned 4 s shots.
        await db.refresh(session)

    assert executed_stages == ["creation_turn", "script", "assets", "compose"]
    assert session.status == "completed"
    runs = list((await db.execute(select(CreationRun).where(CreationRun.session_id == session.id))).scalars())
    assert len(runs) == 1
    project = await db.scalar(select(Project).where(Project.id == runs[0].project_id).options(selectinload(Project.shots)))
    assert project is not None
    assert project.status == ProjectStatus.DONE and project.progress == 100
    assert project.pipeline_mode == "image_text" and project.template_id == "anim_3d"
    assert project.creation_session_id == session.id
    assert image_calls == [1, 2] and audio_calls == [copy_text]
    assert len(project.shots) == 2
    assert all(shot.image_url and shot.audio_url and not shot.video_url for shot in project.shots)
    messages = (await db.execute(select(CreationMessage).where(CreationMessage.session_id == session.id))).scalars()
    assert sum("视频制作完成" in message.content for message in messages) == 1
    tasks = list((await db.execute(select(TaskRun).where(creation._session_task_filter(session.id)))).scalars())
    assert len(tasks) == 4 and all(task.status == "succeeded" for task in tasks)

    final = storage.local_path_from_url(project.final_video_url)
    assert final and final.is_file() and final.stat().st_size > 2000
    probe = json.loads(_media_command([
        ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final),
    ]).stdout)
    video_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert video_stream["width"] * 16 == video_stream["height"] * 9
    assert video_stream["codec_name"] == "h264" and audio_stream["codec_name"] == "aac"
    assert abs(float(video_stream["duration"]) - float(audio_stream["duration"])) < 0.15
    assert 2.9 <= float(probe["format"]["duration"]) <= 3.3
    narration_path = storage.local_path_from_url(project.shots[0].audio_url)
    assert abs(sum(shot.duration for shot in project.shots) - ffmpeg_compose.probe_duration(narration_path)) < 0.02
    assert not ffmpeg_compose.is_near_silent_audio(final)
    visual_filters = [command[command.index("-vf") + 1] for command in ffmpeg_commands if "-vf" in command]
    assert sum("zoompan=" in value for value in visual_filters) == 2
    assert all("drawtext=" in value and "enable='between" in value for value in visual_filters if "zoompan=" in value)
    assert any("蓝色天空" in value for value in visual_filters)
    assert any("阳光" in value for value in visual_filters)
    assert any(event.get("event") == "done" for event in progress_events)
    video_model.assert_not_awaited()
    network.assert_not_awaited()
