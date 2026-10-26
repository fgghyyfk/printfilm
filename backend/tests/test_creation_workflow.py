"""Durable conversation revisions, billing boundaries and automatic media stages."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Project, ProjectStatus, Shot, Template, UsageEvent
from app.models_creation import CreationMessage, CreationRun, CreationSession
from app.models_tasks import TaskRun
from app.schemas_creation import CreationMessageCreate, CreationSessionCreate
from app.services import creation
from app.services.creation_templates import CREATION_TEMPLATE_IDS
from app.services.templates_seed import TEMPLATES
from tests.conftest import make_user


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


async def start(context, *, message="给小学生讲为什么天空是蓝色的"):
    db, user = context
    snapshot = await creation.create_session(db, user, CreationSessionCreate(
        message=message, client_message_id="initial",
    ))
    session = await db.get(CreationSession, snapshot.id)
    task = await db.get(TaskRun, session.active_turn_task_id)
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


async def test_first_message_and_duplicate_submission_are_idempotent(context):
    db, user = context
    session, task = await start(context)
    assert task.task_type == "creation_turn"
    assert task.status == "pending" and task.next_action_at is not None
    same = await creation.create_session(db, user, CreationSessionCreate(
        message="给小学生讲为什么天空是蓝色的", client_message_id="initial",
    ))
    assert same.id == session.id
    await creation.add_message(db, user, session.id, CreationMessageCreate(
        content="给小学生讲为什么天空是蓝色的", client_message_id="initial",
    ))
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == 1
    assert await db.scalar(select(func.count(CreationMessage.id)).where(CreationMessage.session_id == session.id)) == 1
    with pytest.raises(HTTPException) as conflict:
        await creation.add_message(db, user, session.id, CreationMessageCreate(content="换一个完全不同的主题", client_message_id="initial"))
    assert conflict.value.status_code == 409


async def test_asks_one_question_then_uses_conversation_history(context, monkeypatch):
    db, user = context
    session, turn = await start(context, message="帮我做一个视频")
    await creation.execute_creation_turn(turn)
    await succeed(db, turn)
    snapshot = await creation.session_snapshot(db, session.id, user.id)
    assert snapshot.status == "drafting"
    assert not snapshot.runs
    assert "主题" in snapshot.messages[-1].content
    await creation.add_message(db, user, session.id, CreationMessageCreate(content="为什么天空是蓝色的", client_message_id="topic"))
    message_task = await db.get(TaskRun, session.active_turn_task_id)
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
    snapshot = await creation.session_snapshot(db, session.id, user.id)
    assert snapshot.status == "completed"
    assert snapshot.runs[0].phase == "done"
    assert snapshot.runs[0].project.final_video_url.endswith("final.mp4")
    assert len(snapshot.runs[0].project.shots) == 1
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == 4
    assert sum("视频制作完成" in m.content for m in snapshot.messages) == 1


async def test_new_requirement_waits_for_old_writer_then_creates_independent_version(context):
    db, user = context
    session, old_run, old_task = await produce(context)
    old_project_id, old_copy = old_run.project_id, old_run.copy_text
    old_task.status = "running"
    await db.commit()
    await creation.add_message(db, user, session.id, CreationMessageCreate(content="改成温暖的睡前故事语气", client_message_id="revision"))
    new_turn = await db.get(TaskRun, session.active_turn_task_id)
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


async def test_cancel_does_not_resume_without_explicit_retry_and_retry_keeps_materials(context):
    db, user = context
    session, run, task = await produce(context)
    project_id = run.project_id
    shot = Shot(project_id=project_id, shot_no=1, narration="保留已经完成的旁白", img_prompt="3D天空", image_url="/static/kept.png")
    db.add(shot)
    await db.commit()
    await creation.cancel_session(db, user, session.id)
    task.status, task.billing_status = "cancelled", "settled"
    await db.commit()
    before = await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id)))
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == before
    await creation.retry_session(db, user, session.id)
    retried = await db.get(CreationRun, session.active_run_id)
    assert retried.id == run.id and retried.project_id == project_id
    assert retried.phase == "assets"
    assert (await db.get(Shot, shot.id)).image_url == "/static/kept.png"
    first_retry_task = run.current_task_id
    await creation.retry_session(db, user, session.id)
    assert run.current_task_id == first_retry_task


async def test_failed_stage_retries_same_version_and_preserves_error(context):
    db, user = context
    session, run, task = await produce(context)
    task.status, task.billing_status, task.error_message = "failed", "settled", "余额不足"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    snapshot = await creation.session_snapshot(db, session.id, user.id)
    assert snapshot.status == "failed" and snapshot.runs[0].error_message == "余额不足"
    await creation.retry_session(db, user, session.id)
    assert run.version == 1 and run.current_task_id != task.id


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


async def test_owner_isolation_and_rejects_photoreal_templates(context):
    db, user = context
    session, _task = await start(context)
    stranger = await make_user(db)
    for action in (
        lambda: creation.session_snapshot(db, session.id, stranger.id),
        lambda: creation.cancel_session(db, stranger, session.id),
        lambda: creation.retry_session(db, stranger, session.id),
        lambda: creation.add_message(db, stranger, session.id, CreationMessageCreate(content="新的主题", client_message_id="foreign")),
    ):
        with pytest.raises(HTTPException) as denied:
            await action()
        assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as invalid:
        await creation.create_session(db, user, CreationSessionCreate(template_id="live_person"))
    assert invalid.value.status_code == 400


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
    await creation.add_message(db, user, session.id, CreationMessageCreate(
        content="改为国风，换女声，半分钟", client_message_id="chip-overrides-text", template_id="kids_flat",
    ))
    turn2 = await db.get(TaskRun, session.active_turn_task_id)
    await creation.execute_creation_turn(turn2)
    revised = await db.get(CreationRun, session.active_run_id)
    revised_project = await db.get(Project, revised.project_id)
    assert revised_project.template_id == "kids_flat"
    assert "female" in revised_project.voice_id
    assert revised_project.target_duration_sec == 30
    assert revised_project.title == project.title
    assert creation._brief({"voice_id": "unconfigured-secret-voice"})["voice_id"] == ""


async def test_recent_sessions_include_drafts_and_snapshot_is_read_only(context):
    db, user = context
    session, _turn = await start(context)
    draft = await creation.create_session(db, user, CreationSessionCreate())
    stranger = await make_user(db)
    await db.commit()
    own = await creation.list_sessions(db, user.id, 5)
    assert {item.id for item in own.items} == {session.id, draft.id}
    assert (await creation.list_sessions(db, stranger.id)).items == []
    dirty_before = set(db.dirty)
    await creation.session_snapshot(db, session.id, user.id)
    assert set(db.dirty) == dirty_before


async def test_reconcile_cursor_does_not_starve_later_pending_conversations(context, monkeypatch):
    db, user = context
    monkeypatch.setattr(creation, "_reconcile_cursor", 0)
    sessions = []
    for index in range(5):
        row = CreationSession(user_id=user.id, template_id="anim_3d", status="thinking")
        db.add(row)
        await db.flush()
        message = CreationMessage(session_id=row.id, role="user", content=f"第{index}个天空故事")
        db.add(message)
        await db.flush()
        task = await creation._new_turn_task(db, user, row, message)
        row.latest_message_id = message.id
        if index < 4:
            task.status = "running"
        sessions.append((row, task))
    await db.commit()
    # Older long-running turns fill both first pages; the fifth still activates.
    for _ in range(3):
        await creation.reconcile_creation_sessions(db, limit=2)
    assert sessions[-1][1].next_action_at is not None


async def test_retry_waits_for_cancellation_owner_before_resetting_project(context):
    from app.services import pipeline
    db, user = context
    session, run, task = await produce(context)
    task.status = "running"
    task.lease_until = datetime.now(UTC) + timedelta(minutes=10)
    await db.commit()
    await creation.cancel_session(db, user, session.id)
    project = await db.get(Project, run.project_id)
    pipeline.cancel_pipeline(project.id)
    await creation.retry_session(db, user, session.id)
    assert project.status == ProjectStatus.CANCELLED
    assert run.current_task_id is None
    task.status, task.billing_status = "cancelled", "settled"
    await db.commit()
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert project.status == ProjectStatus.SCRIPTING
    assert not pipeline.is_cancelled(project.id)
    assert run.current_task_id is not None


async def test_sse_uses_captured_owner_id_after_rollback(context, monkeypatch):
    from app.api import creation as api
    db, user = context
    session, _turn = await start(context)
    session_id, user_id = session.id, user.id
    monkeypatch.setattr(api, "AsyncSessionLocal", creation.AsyncSessionLocal)
    monkeypatch.setattr(api.asyncio, "sleep", AsyncMock())
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
    snapshot_mock = AsyncMock(wraps=creation.session_snapshot)
    monkeypatch.setattr(creation, "session_snapshot", snapshot_mock)
    response = await api.events(session_id, request, db, user)
    first = await anext(response.body_iterator)
    assert first["event"] == "session"
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)
    assert snapshot_mock.await_args_list[-1].args[-1] == user_id


async def test_cancelled_clarification_retry_restores_question_without_another_charge(context):
    db, user = context
    session, task = await start(context, message="帮我做一个视频")
    await creation.execute_creation_turn(task)
    await succeed(db, task)
    await creation.cancel_session(db, user, session.id)
    await creation.retry_session(db, user, session.id)
    assert session.status == "drafting"
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == 1


async def test_http_contract_and_owner_isolation(context):
    import httpx
    from app.database import get_db
    from app.deps import get_current_user
    from app.main import app
    db, user = context

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
            created = await client.post("/api/creation-sessions", json={"message": "给孩子讲天空为什么是蓝色的", "client_message_id": "http-initial"})
            assert created.status_code == 200
            session_id = created.json()["id"]
            assert created.json()["status"] == "thinking"
            assert created.json()["brief"]["target_duration_sec"] == 60
            recent = await client.get("/api/creation-sessions?limit=5")
            assert recent.status_code == 200 and recent.json()["items"][0]["id"] == session_id
            detail = await client.get(f"/api/creation-sessions/{session_id}")
            assert detail.status_code == 200 and len(detail.json()["messages"]) == 1
            invalid = await client.post(f"/api/creation-sessions/{session_id}/messages", json={"content": " ", "client_message_id": "blank"})
            assert invalid.status_code == 422
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user.id + 1000000)
            assert (await client.get(f"/api/creation-sessions/{session_id}")).status_code == 404
            assert (await client.post(f"/api/creation-sessions/{session_id}/cancel")).status_code == 404
            assert (await client.get(f"/api/creation-sessions/{session_id}/events")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
