"""Accepted inline generations remain visible throughout their lifecycle."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models_tasks import TaskRun
from app.services.billing import ephemeral


@pytest.fixture
def voice_generation_enabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "drama_voice_generation_enabled", True)


def inline_runtime(monkeypatch):
    task = TaskRun(id=42, status="pending", payload={})
    db = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(ephemeral, "create_ephemeral_task_row", AsyncMock(return_value=task))
    monkeypatch.setattr(ephemeral, "freeze_for_task", AsyncMock())
    monkeypatch.setattr(ephemeral, "settle_task", AsyncMock())
    monkeypatch.setattr(ephemeral, "append_task_event", AsyncMock())
    return db, task


@pytest.mark.asyncio
async def test_inline_generation_is_committed_before_external_io(monkeypatch):
    db, task = inline_runtime(monkeypatch)

    async def execute():
        db.commit.assert_awaited_once()
        assert task.status == "running"
        return "新的创作"

    run = ephemeral.run_billed_ephemeral
    await run(db, SimpleNamespace(id=1), domain="studio", task_type="tool_video", executor=execute, commit=False)
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_inline_failure_remains_after_callers_rollback(monkeypatch):
    db, task = inline_runtime(monkeypatch)
    run = ephemeral.run_billed_ephemeral
    with pytest.raises(RuntimeError, match="生成失败"):
        await run(db, SimpleNamespace(id=1), domain="studio", task_type="tool_video",
                  executor=AsyncMock(side_effect=RuntimeError("生成失败")), commit=False)
    assert task.status == "failed"
    assert task.finished_at is not None
    assert task.error_message == "生成失败"
    assert db.commit.await_count == 2
    ephemeral.create_ephemeral_task_row.assert_awaited_once()
    ephemeral.settle_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_unbilled_generation_tracks_output_without_new_charges(monkeypatch, voice_generation_enabled):
    db, task = inline_runtime(monkeypatch)
    _, result = await ephemeral.run_billed_ephemeral(
        db, SimpleNamespace(id=1), domain="drama", task_type="voice_synthesis",
        executor=AsyncMock(return_value="/static/voice.mp3"), billable=False,
        result_payload_factory=lambda url: {"kind": "audio", "urls": [url]},
    )
    assert result == "/static/voice.mp3"
    assert task.status == "succeeded"
    assert task.result_payload == {"ok": True, "kind": "audio", "urls": [result]}
    ephemeral.freeze_for_task.assert_not_awaited()
    ephemeral.settle_task.assert_not_awaited()
    ephemeral.create_ephemeral_task_row.assert_awaited_once()


@pytest.mark.asyncio
async def test_inline_cancellation_is_persisted_and_unfreezes_once(monkeypatch):
    db, task = inline_runtime(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        await ephemeral.run_billed_ephemeral(
            db, SimpleNamespace(id=1), domain="drama", task_type="agent_chat",
            executor=AsyncMock(side_effect=asyncio.CancelledError()), commit=False,
        )
    assert task.status == "cancelled"
    assert task.finished_at is not None
    assert db.commit.await_count == 2
    ephemeral.settle_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_generated_text_is_saved_in_task_result(monkeypatch):
    db, task = inline_runtime(monkeypatch)
    await ephemeral.run_billed_ephemeral(
        db, SimpleNamespace(id=1), domain="drama", task_type="agent_chat",
        executor=AsyncMock(return_value="新的创作"),
    )
    assert task.result_payload["kind"] == "text"
    assert task.result_payload["text_content"] == "新的创作"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_personal_voice_generation_has_one_durable_task_without_charging(
    db_session, monkeypatch, fail, voice_generation_enabled,
):
    from app.api.drama import generation
    from app.models_drama import DramaAsset, DramaProject
    from app.schemas_drama import DramaVoiceGenerateRequest
    from tests.conftest import make_user

    user = await make_user(db_session, balance_fen=0)
    user_id = user.id
    project = DramaProject(user_id=user.id)
    db_session.add(project)
    await db_session.flush()
    asset = DramaAsset(project_id=project.id, type="voice", asset_type="audio", params={})
    db_session.add(asset)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_owned_drama_project", AsyncMock(return_value=project))

    async def synthesize(*_args, **_kwargs):
        tasks = list((await db_session.execute(select(TaskRun).where(TaskRun.requested_by == user.id))).scalars())
        assert len(tasks) == 1 and tasks[0].status == "running"
        if fail:
            raise RuntimeError("TTS unavailable")
        asset.url = "/static/generated-voice.mp3"
        await db_session.commit()
        await db_session.refresh(asset)
        return asset

    monkeypatch.setattr(generation, "generate_voice_asset_audio", synthesize)
    request = DramaVoiceGenerateRequest(project_id=project.id, asset_id=asset.id, voice_prompt="沉稳的声音")
    if fail:
        with pytest.raises(HTTPException, match="TTS unavailable"):
            await generation.generate_voice(request, db=db_session, user=user)
    else:
        await generation.generate_voice(request, db=db_session, user=user)
    # A request teardown rollback cannot remove the completed/failed task.
    await db_session.rollback()
    task = (await db_session.execute(select(TaskRun).where(TaskRun.requested_by == user_id))).scalar_one()
    assert task.status == ("failed" if fail else "succeeded")
    assert task.finished_at and task.started_at
    assert task.billing_status == "skipped"
    await db_session.refresh(user)
    assert user.balance_fen == 0 and user.frozen_fen == 0
    if not fail:
        assert task.result_payload["urls"] == ["/static/generated-voice.mp3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [None, 91])
async def test_episode_compose_registers_one_generation_and_keeps_billing_policy(monkeypatch, team_id):
    from app.api.drama import episodes
    from app.services.drama import episode_compose

    project = SimpleNamespace(id=3, team_id=team_id)
    episode = SimpleNamespace(id=7, project_id=3)
    monkeypatch.setattr(episodes, "get_owned_episode", AsyncMock(return_value=episode))
    monkeypatch.setattr(episodes, "get_owned_drama_project", AsyncMock(return_value=project))
    monkeypatch.setattr(episode_compose, "load_episode_for_compose", AsyncMock(return_value=episode))
    compose = AsyncMock(return_value="/static/composed.mp4")
    monkeypatch.setattr(episode_compose, "compose_episode_video", compose)

    async def register(*_args, **kwargs):
        assert kwargs["task_type"] == "episode_compose"
        assert kwargs["billable"] is (team_id is not None)
        result = await kwargs["executor"]()
        assert kwargs["result_payload_factory"](result) == {"kind": "video", "urls": [result]}
        return SimpleNamespace(id=1), result

    register_task = AsyncMock(side_effect=register)
    monkeypatch.setattr(episodes, "run_billed_ephemeral", register_task)
    result = await episodes.compose_episode(7, db=SimpleNamespace(), user=SimpleNamespace(id=1))
    assert result["video_url"] == "/static/composed.mp4"
    register_task.assert_awaited_once()
    compose.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_audition_registers_audio_task_without_charging(monkeypatch):
    from app.api import projects
    from app.schemas import VoicePreviewRequest

    preview = AsyncMock(return_value="/static/preview.mp3")
    monkeypatch.setattr(projects, "ensure_voice_preview", preview)

    async def register(*_args, **kwargs):
        assert kwargs["task_type"] == "voice_preview" and kwargs["billable"] is False
        result = await kwargs["executor"]()
        assert kwargs["result_payload_factory"](result) == {"kind": "audio", "urls": [result]}
        return SimpleNamespace(id=1), result

    registered = AsyncMock(side_effect=register)
    monkeypatch.setattr(projects, "run_billed_ephemeral", registered)
    result = await projects.preview_voice(VoicePreviewRequest(voice_id="narrator"), db=SimpleNamespace(), user=SimpleNamespace(id=1))
    assert result.url == "/static/preview.mp3"
    registered.assert_awaited_once()
    preview.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type,phase,kind,url", [
    ("shot_regen_image", None, "image", "/static/output.png"),
    ("shot_regen_video", None, "video", "/static/output.mp4"),
    ("shot_regen_audio", None, "audio", "/static/output.mp3"),
    ("project_pipeline", "assets", "image", "/static/output.png"),
    ("project_compose_only", None, "video", "/static/final.mp4"),
])
async def test_kepu_output_snapshot_freezes_only_matching_media(db_session, monkeypatch, task_type, phase, kind, url):
    from app.models import Project, Shot, Template
    from app.services.tasks import handlers
    from tests.conftest import make_user

    user = await make_user(db_session)
    template = Template(id=f"output-{task_type}-{phase}", name="test", style_prefix="test")
    db_session.add(template)
    await db_session.flush()
    project = Project(user_id=user.id, template_id=template.id, source_text="input", final_video_url="/static/final.mp4")
    db_session.add(project)
    await db_session.flush()
    shot = Shot(project_id=project.id, shot_no=1, image_url="/static/output.png", video_url="/static/output.mp4", audio_url="/static/output.mp3")
    db_session.add(shot)
    await db_session.flush()

    @asynccontextmanager
    async def session_factory():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_factory)
    result = await handlers._kepu_output_snapshot(TaskRun(task_type=task_type, project_id=project.id, shot_id=shot.id), phase)
    assert result == {"kind": kind, "urls": [url]}
    shot.image_url = "/static/replaced.png"
    assert result["urls"] == [url]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,ready,mode,expected_phase,kind,url", [
    (None, "empty", "full", "script", "text", None),
    ("produce", "empty", "full", "script", "text", None),
    (None, "script", "full", "assets", "image", "/static/generated.png"),
    ("produce", "script", "full", "assets", "image", "/static/generated.png"),
    (None, "assets", "full", "videos", "video", "/static/generated.mp4"),
    ("produce", "assets", "full", "videos", "video", "/static/generated.mp4"),
    (None, "videos", "full", "compose", "video", "/static/generated-final.mp4"),
    ("produce", "videos", "full", "compose", "video", "/static/generated-final.mp4"),
    (None, "assets", "image_text", "compose", "video", "/static/generated-final.mp4"),
    ("script", "assets", "full", "script", "text", None),
    ("assets", "script", "full", "assets", "image", "/static/generated.png"),
    ("videos", "assets", "full", "videos", "video", "/static/generated.mp4"),
    ("compose", "videos", "full", "compose", "video", "/static/generated-final.mp4"),
])
async def test_pipeline_legacy_phase_runs_and_snapshots_the_same_resolved_stage(
    monkeypatch, phase, ready, mode, expected_phase, kind, url,
):
    from app.services import pipeline
    from app.services.tasks import handlers

    shots = [] if ready == "empty" else [SimpleNamespace(
        image_url="/static/image.png" if ready in {"assets", "videos"} else None,
        video_url="/static/video.mp4" if ready == "videos" else None,
        narration="完整脚本", segment_script=None,
    )]
    project = SimpleNamespace(id=3, shots=shots, pipeline_mode=mode, cover_url=None, final_video_url=None)
    monkeypatch.setattr("app.services.kepu_stages.project_audio_ready", lambda _project: True)
    result_rows = SimpleNamespace(scalar_one_or_none=lambda: project, scalars=lambda: SimpleNamespace(all=lambda: shots))
    db = SimpleNamespace(get=AsyncMock(return_value=project), execute=AsyncMock(return_value=result_rows))
    opened = 0

    @asynccontextmanager
    async def session_factory():
        nonlocal opened
        opened += 1
        try:
            yield db
        finally:
            opened -= 1

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_factory)

    async def run(project_id, *, phase):
        # Stage selection must return its read connection before any provider wait.
        assert opened == 0
        assert project_id == project.id and phase == expected_phase
        if phase == "script" and not shots:
            shots.append(SimpleNamespace(narration="完整脚本", segment_script=None))
        elif phase == "assets":
            shots[0].image_url = url
        elif phase == "videos":
            shots[0].video_url = url
        elif phase == "compose":
            project.final_video_url = url

    stage = AsyncMock(side_effect=run)
    monkeypatch.setattr(pipeline, "run_pipeline", stage)
    task = TaskRun(task_type="project_pipeline", project_id=project.id, cancel_requested=False,
                   payload={} if phase is None else {"phase": phase})
    original_payload = dict(task.payload)
    result = await handlers._run_kepu_project_pipeline(task)
    stage.assert_awaited_once()
    assert result["phase"] == expected_phase
    assert result["kind"] == kind
    if kind == "text":
        assert result["text_content"] == "完整脚本"
    else:
        assert result["urls"] == [url]
    assert task.payload == original_payload  # No new task or billing dimensions are introduced.
    assert opened == 0


@pytest.mark.asyncio
async def test_cancelled_legacy_pipeline_does_not_resolve_or_run_another_stage(monkeypatch):
    from app.services import pipeline
    from app.services.tasks import handlers

    session_factory = AsyncMock(side_effect=AssertionError("cancelled tasks must not query their next stage"))
    monkeypatch.setattr("app.database.AsyncSessionLocal", session_factory)
    stage = AsyncMock()
    monkeypatch.setattr(pipeline, "run_pipeline", stage)
    task = TaskRun(task_type="project_pipeline", project_id=3, cancel_requested=True, payload={"phase": "produce"})
    assert await handlers._run_kepu_project_pipeline(task) == {"cancelled": True, "project_id": 3}
    session_factory.assert_not_called()
    stage.assert_not_awaited()
