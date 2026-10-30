"""Persisted legacy turns still drain safely across restarts and billing settlement."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Project, ProjectStatus, Shot, Template, UsageEvent
from app.models_creation import CreationMessage, CreationRun, CreationSession
from app.models_tasks import TaskRun
from app.services import creation
from app.services.creation_templates import CREATION_TEMPLATE_IDS
from app.services.templates_seed import TEMPLATES
from tests.conftest import make_task, make_user


@pytest.fixture
async def context(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    for data in TEMPLATES:
        if data["id"] in CREATION_TEMPLATE_IDS and await db.get(Template, data["id"]) is None:
            db.add(Template(**data))
    await db.flush()

    @asynccontextmanager
    async def local_session():
        yield db

    monkeypatch.setattr(creation, "AsyncSessionLocal", local_session)
    monkeypatch.setattr("app.services.kepu_stages.project_audio_ready", lambda p: bool(p.shots) and all(s.audio_url for s in p.shots))
    user = await make_user(db)
    return db, user


async def persisted_turn(db, user, session, content, *, requested_template_id=None):
    """Seed a pre-retirement message/task pair; no public submission API exists."""
    message = CreationMessage(session_id=session.id, role="user", content=content)
    db.add(message)
    await db.flush()
    task = await make_task(db, user, domain="kepu", task_type="creation_turn")
    task.payload = {"creation_session_id": session.id, "message_id": message.id,
                    "requested_template_id": requested_template_id}
    message.task_id = task.id
    session.latest_message_id = message.id
    session.active_turn_task_id = task.id
    session.status = "thinking"
    await db.commit()
    return task


async def start(context, *, message="给小学生讲为什么天空是蓝色的"):
    """Restore a historical queued session and let the reconciler activate it."""
    db, user = context
    session = CreationSession(user_id=user.id, template_id="anim_3d", brief={})
    db.add(session)
    await db.flush()
    task = await persisted_turn(db, user, session, message)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    return session, task


async def succeed(db, task, *, settled=True):
    task.status = "succeeded"
    task.billing_status = "settled" if settled else "frozen"
    await db.commit()


async def produce(context):
    db, _user = context
    session, turn = await start(context)
    await creation.execute_creation_turn(turn)
    await succeed(db, turn)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    run = await db.get(CreationRun, session.active_run_id)
    return session, run, await db.get(TaskRun, run.current_task_id)


async def test_asks_one_question_then_uses_conversation_history(context, monkeypatch):
    db, user = context
    session, turn = await start(context, message="帮我做一个视频")
    await creation.execute_creation_turn(turn)
    await succeed(db, turn)
    assert session.status == "drafting" and session.active_run_id is None
    reply = await db.scalar(select(CreationMessage).where(
        CreationMessage.session_id == session.id, CreationMessage.role == "assistant",
    ))
    assert "主题" in reply.content
    message_task = await persisted_turn(db, user, session, "为什么天空是蓝色的")
    seen = []

    async def planner(_system, user_context, **_kwargs):
        import json
        seen.extend(json.loads(user_context)["history"])
        return {"action": "produce", "title": "蓝色天空", "copy_text": "太阳光包含不同颜色，进入大气后，较短波长的蓝光更容易向各个方向散射。", "brief": {}}

    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr("app.services.drama.llm.drama_chat_json", planner)
    await creation.execute_creation_turn(message_task)
    assert [item["role"] for item in seen] == ["user", "assistant", "user"]
    assert seen[0]["content"] == "帮我做一个视频"


async def test_auto_production_advances_only_after_settlement_and_once(context):
    db, user = context
    session, turn = await start(context)
    await creation.execute_creation_turn(turn)
    run = await db.get(CreationRun, session.active_run_id)
    project = await db.get(Project, run.project_id)
    assert project.pipeline_mode == "image_text" and project.source_type == "script"
    assert project.output_ratio == "9:16"
    assert project.target_duration_sec == 60
    assert project.creation_session_id == session.id
    await succeed(db, turn, settled=False)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert run.phase == "planning"
    turn.billing_status = "settled"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    script_task_id = run.current_task_id
    assert run.phase == "script"
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert run.current_task_id == script_task_id
    shot = Shot(project_id=project.id, shot_no=1, narration=run.copy_text, img_prompt="3D小角色望着天空")
    db.add(shot)
    project.status = ProjectStatus.SCRIPT_READY
    await succeed(db, await db.get(TaskRun, script_task_id))
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert run.phase == "assets"
    assets_task = await db.get(TaskRun, run.current_task_id)
    assert assets_task.payload["phase"] == "assets"
    shot.image_url, shot.audio_url = "/static/test/image.png", "/static/test/audio.mp3"
    await succeed(db, assets_task)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert run.phase == "compose"
    compose_task = await db.get(TaskRun, run.current_task_id)
    assert compose_task.task_type == "project_compose_only"
    project.status, project.final_video_url = ProjectStatus.DONE, "/static/test/final.mp4"
    await succeed(db, compose_task)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert session.status == "completed" and run.phase == "done"
    assert project.final_video_url.endswith("final.mp4")
    assert await db.scalar(select(func.count(Shot.id)).where(Shot.project_id == project.id)) == 1
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == 4
    messages = (await db.execute(select(CreationMessage).where(CreationMessage.session_id == session.id))).scalars()
    assert sum("视频制作完成" in message.content for message in messages) == 1


async def test_new_requirement_waits_for_old_writer_then_creates_independent_version(context):
    db, user = context
    session, old_run, old_task = await produce(context)
    old_project_id, old_copy = old_run.project_id, old_run.copy_text
    old_task.status = "running"
    await db.commit()
    # A newer turn had already superseded this producer before retirement.
    old_run.status = "cancelled"
    old_task.cancel_requested = True
    new_turn = await persisted_turn(db, user, session, "改成温暖的睡前故事语气")
    assert old_run.status == "cancelled" and old_task.cancel_requested
    assert new_turn.next_action_at is None
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert new_turn.next_action_at is None
    old_task.status, old_task.billing_status = "cancelled", "settled"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert new_turn.next_action_at is not None
    await creation.execute_creation_turn(new_turn)
    new_run = await db.get(CreationRun, session.active_run_id)
    assert new_run.version == 2 and new_run.project_id != old_project_id
    assert old_run.copy_text == old_copy


async def test_failed_stage_stays_stopped_and_preserves_error_and_materials(context):
    db, user = context
    session, run, task = await produce(context)
    task.status, task.billing_status, task.error_message = "failed", "settled", "余额不足"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert session.status == "failed" and run.error_message == "余额不足"
    assert await creation.reconcile_creation_sessions(db, session_id=session.id) == 0
    assert run.version == 1 and run.current_task_id == task.id
    assert (await db.get(Project, run.project_id)).source_text == run.copy_text


async def test_persisted_turn_result_is_reused_without_duplicate_llm_or_usage(context, monkeypatch):
    db, user = context
    session, task = await start(context)
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    planner = AsyncMock(return_value={
        "action": "produce", "title": "蓝色天空", "copy_text": "太阳光进入大气后，蓝光向不同方向散射。抬头望向天空，我们就能看到这些散射的蓝色光。", "brief": {},
    })
    monkeypatch.setattr("app.services.drama.llm.drama_chat_json", planner)
    await creation.execute_creation_turn(task)
    restored = await creation.execute_creation_turn(task)
    assert restored["restored"]
    assert planner.await_count == 1
    assert await db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.task_run_id == task.id)) == 1
    assert await db.scalar(select(func.count(CreationRun.id)).where(CreationRun.session_id == session.id)) == 1


async def test_natural_language_voice_style_duration_and_explicit_chip_precedence(context):
    db, user = context
    session, turn = await start(context, message="给孩子讲月亮的故事，换成国风水墨，用男声，90秒横屏")
    await creation.execute_creation_turn(turn)
    run = await db.get(CreationRun, session.active_run_id)
    project = await db.get(Project, run.project_id)
    assert project.template_id == session.template_id == "ink_guofeng"
    assert "male" in project.voice_id and "female" not in project.voice_id
    assert project.target_duration_sec == 90 and project.output_ratio == "16:9"
    await succeed(db, turn)
    turn2 = await persisted_turn(db, user, session, "改为国风，换女声，半分钟", requested_template_id="kids_flat")
    await creation.execute_creation_turn(turn2)
    revised = await db.get(CreationRun, session.active_run_id)
    revised_project = await db.get(Project, revised.project_id)
    assert revised_project.template_id == "kids_flat"
    assert "female" in revised_project.voice_id
    assert revised_project.target_duration_sec == 30
    assert revised_project.title == project.title
    assert creation._brief({"voice_id": "unconfigured-secret-voice"})["voice_id"] == ""


async def test_reconcile_cursor_does_not_starve_later_pending_conversations(context, monkeypatch):
    db, user = context
    monkeypatch.setattr(creation, "_reconcile_cursor", 0)
    sessions = []
    for index in range(5):
        row = CreationSession(user_id=user.id, template_id="anim_3d", status="thinking")
        db.add(row)
        await db.flush()
        task = await persisted_turn(db, user, row, f"第{index}个天空故事")
        if index < 4:
            task.status = "running"
        sessions.append((row, task))
    await db.commit()
    # Older long-running turns fill both first pages; the fifth still activates.
    for _ in range(3):
        await creation.reconcile_creation_sessions(db, limit=2)
    assert sessions[-1][1].next_action_at is not None


async def test_persisted_resume_waits_for_cancellation_owner_before_resetting_project(context):
    from app.services import pipeline
    db, user = context
    session, run, task = await produce(context)
    task.status = "running"
    task.lease_until = datetime.now(UTC) + timedelta(minutes=10)
    await db.commit()
    # This resume request was persisted before the conversation API retired.
    task.status, task.cancel_requested = "cancel_requested", True
    run.current_task_id = None
    project = await db.get(Project, run.project_id)
    project.status = ProjectStatus.CANCELLED
    pipeline.cancel_pipeline(project.id)
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert project.status == ProjectStatus.CANCELLED
    assert run.current_task_id is None
    task.status, task.billing_status = "cancelled", "settled"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert project.status == ProjectStatus.SCRIPTING
    assert not pipeline.is_cancelled(project.id)
    assert run.current_task_id is not None
