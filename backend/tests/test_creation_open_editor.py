"""A conversation can become an ordinary wizard project without racing writers."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.projects import create_project, update_project
from app.models import Project, ProjectStatus, Shot
from app.models_creation import CreationMessage, CreationRun, CreationSession
from app.models_tasks import TaskRun
from app.schemas import ProjectCreate, ProjectUpdate
from app.services import creation, pipeline
from app.services.ark import StoryboardResult
from tests.conftest import make_task, make_user
from tests.test_creation_workflow import context, persisted_turn, produce, start, succeed  # noqa: F401


async def test_handoff_waits_for_terminal_and_settled_writer_then_keeps_materials(context):
    db, user = context
    session, run, task = await produce(context)
    project = await db.get(Project, run.project_id)
    shot = Shot(project_id=project.id, shot_no=1, narration="已有旁白", img_prompt="天空", image_url="/kept.png")
    db.add(shot)
    task.status = "running"
    await db.commit()
    task_count = await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id)))

    with pytest.raises(HTTPException) as waiting:
        await creation.open_editor(db, user, session.id)
    assert waiting.value.status_code == 409
    assert waiting.value.headers["Retry-After"] == "2"
    assert task.cancel_requested and session.status == "editor_pending"
    assert project.creation_session_id == session.id
    await creation.reconcile_creation_sessions(db, session_id=session.id)
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == task_count

    task.status, task.billing_status = "cancelled", "frozen"
    await db.commit()
    with pytest.raises(HTTPException) as settling:
        await creation.open_editor(db, user, session.id)
    assert settling.value.status_code == 409
    task.billing_status = "settled"
    await db.commit()
    handed = await creation.open_editor(db, user, session.id)
    assert handed.id == project.id and handed.creation_session_id is None
    assert handed.status == ProjectStatus.SCRIPT_READY
    assert handed.shots[0].image_url == "/kept.png"
    assert session.status == "editor"

    # Opening the legacy URL twice must not cancel a subsequent wizard task.
    project.status = ProjectStatus.IMAGING
    project.error_msg = "new wizard progress"
    await db.commit()
    repeated = await creation.open_editor(db, user, session.id)
    assert repeated.id == handed.id and repeated.status == ProjectStatus.IMAGING
    assert repeated.error_msg == "new wizard progress"
    assert await db.scalar(select(func.count(TaskRun.id)).where(creation._session_task_filter(session.id))) == task_count


async def test_empty_or_foreign_session_cannot_create_editor_project(context):
    db, user = context
    session = CreationSession(user_id=user.id, template_id="anim_3d", brief={})
    db.add(session)
    await db.commit()
    session_id = session.id
    stranger = await make_user(db)
    with pytest.raises(HTTPException) as forbidden:
        await creation.open_editor(db, stranger, session_id)
    assert forbidden.value.status_code == 404
    with pytest.raises(HTTPException) as empty:
        await creation.open_editor(db, user, session_id)
    assert empty.value.status_code == 400


async def test_draft_handoff_reuses_one_project_and_prevents_late_turn(context):
    db, user = context
    session, turn = await start(context)
    with pytest.raises(HTTPException):
        await creation.open_editor(db, user, session.id)
    # The queued planner may still get a late executor callback after cancellation.
    assert (await creation.execute_creation_turn(turn))["cancelled"]
    turn.status, turn.billing_status = "cancelled", "settled"
    await db.commit()
    project = await creation.open_editor(db, user, session.id)
    again = await creation.open_editor(db, user, session.id)
    assert again.id == project.id
    assert project.status == ProjectStatus.DRAFT and project.source_type == "theme"
    assert project.source_text == "给小学生讲为什么天空是蓝色的"
    assert project.pipeline_mode == "image_text" and project.output_ratio == "9:16"
    assert await db.scalar(select(func.count(Project.id)).where(Project.user_id == user.id)) == 1
    assert (await creation.execute_creation_turn(turn))["cancelled"]
    assert await creation.reconcile_creation_sessions(db, session_id=session.id) == 0
    assert session.status == "editor"


async def test_completed_video_and_copy_are_preserved(context):
    db, user = context
    session, run, task = await produce(context)
    await succeed(db, task)
    project = await db.get(Project, run.project_id)
    project.status, project.final_video_url = ProjectStatus.DONE, "/finished.mp4"
    session.status, run.status = "completed", "completed"
    await db.commit()
    result = await creation.open_editor(db, user, session.id)
    assert result.final_video_url == "/finished.mp4" and result.status == ProjectStatus.DONE
    assert result.source_text == run.copy_text


async def test_selecting_old_version_keeps_other_wizard_versions_independent(context):
    db, user = context
    session, first_run, task = await produce(context)
    await succeed(db, task)
    old_project_id = first_run.project_id
    turn = await persisted_turn(db, user, session, "再做一个温暖的版本")
    await creation.execute_creation_turn(turn)
    await succeed(db, turn)
    latest_run = await db.get(CreationRun, session.active_run_id)
    assert latest_run.project_id != old_project_id

    old_version = await creation.open_editor(db, user, session.id, project_id=old_project_id)
    assert old_version.id == old_project_id and old_version.creation_session_id is None
    old_project = await db.get(Project, old_project_id)
    old_project.status = ProjectStatus.IMAGING
    wizard_task = await make_task(db, user, domain="kepu", task_type="project_pipeline", status="running")
    wizard_task.project_id = old_project_id
    unsettled = await make_task(db, user, domain="kepu", task_type="project_pipeline", status="succeeded", billing_status="frozen")
    unsettled.project_id = latest_run.project_id
    unsettled.payload = {"creation_session_id": session.id}
    await db.commit()
    with pytest.raises(HTTPException) as waiting:
        await creation.open_editor(db, user, session.id, project_id=latest_run.project_id)
    assert waiting.value.status_code == 409 and session.status == "editor"
    assert not wizard_task.cancel_requested
    unsettled.billing_status = "settled"
    await db.commit()
    latest_version = await creation.open_editor(db, user, session.id, project_id=latest_run.project_id)
    assert latest_version.id == latest_run.project_id and latest_version.creation_session_id is None
    assert old_project.status == ProjectStatus.IMAGING and not wizard_task.cancel_requested
    assert (await creation.open_editor(db, user, session.id)).id == old_project_id
    assert (await creation.open_editor(db, user, session.id, project_id=latest_run.project_id)).id == latest_version.id


async def test_selected_version_must_belong_to_owner_and_session_before_cancellation(context):
    db, user = context
    session, _run, task = await produce(context)
    other_session, other_turn = await start(context, message="另一段独立故事")
    await creation.execute_creation_turn(other_turn)
    other_run = await db.get(CreationRun, other_session.active_run_id)
    stranger = await make_user(db)
    foreign_project = Project(user_id=stranger.id, template_id="anim_3d", source_text="他人作品", status=ProjectStatus.DRAFT)
    db.add(foreign_project)
    await db.commit()
    for invalid_id in (other_run.project_id, foreign_project.id, 100000000):
        with pytest.raises(HTTPException) as rejected:
            await creation.open_editor(db, user, session.id, project_id=invalid_id)
        assert rejected.value.status_code == 404
        assert not task.cancel_requested and session.status == "generating"


async def test_saved_copy_without_project_becomes_editable_script(context):
    db, user = context
    session, turn = await start(context)
    message = await db.get(CreationMessage, session.latest_message_id)
    message.response_payload = {"action": "produce", "copy_text": "这是已经生成并保存的完整文案。"}
    await succeed(db, turn)
    result = await creation.open_editor(db, user, session.id)
    assert result.source_type == "script" and result.source_text == message.response_payload["copy_text"]


async def test_inflight_planner_cannot_publish_a_run_after_handoff_request(context, monkeypatch):
    from app.config import get_settings
    db, user = context
    session, turn = await start(context)
    monkeypatch.setattr(get_settings(), "ark_mock", False)

    async def response_after_handoff(*_args, **_kwargs):
        with pytest.raises(HTTPException) as waiting:
            await creation.open_editor(db, user, session.id)
        assert waiting.value.status_code == 409
        return {"action": "produce", "title": "迟到的结果", "copy_text": "这是已停止的旧模型请求返回的文案，不应创建新的自动制作版本。"}

    monkeypatch.setattr("app.services.drama.llm.drama_chat_json", response_after_handoff)
    assert (await creation.execute_creation_turn(turn))["cancelled"]
    assert session.status == "editor_pending"
    assert await db.scalar(select(func.count(CreationRun.id)).where(CreationRun.session_id == session.id)) == 0
    await succeed(db, turn)
    project = await creation.open_editor(db, user, session.id)
    assert project.source_type == "theme" and "迟到" not in project.source_text


async def test_open_editor_http_contract(context):
    import httpx
    from app.database import get_db
    from app.deps import get_current_user
    from app.main import app
    db, user = context
    session, turn = await start(context)

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
            url = f"/api/creation-sessions/{session.id}/open-editor"
            assert (await client.post(url + "?project_id=-1")).status_code == 422
            assert (await client.post(url + "?project_id=100000000")).status_code == 404
            assert not turn.cancel_requested
            response = await client.post(url)
            assert response.status_code == 409 and response.headers["Retry-After"] == "2"
            turn.status, turn.billing_status = "cancelled", "settled"
            await db.commit()
            response = await client.post(url)
            assert response.status_code == 200
            assert response.json()["creation_session_id"] is None
            assert response.json()["status"] == "DRAFT"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_wizard_duration_persists_and_reaches_storyboard_prompt(context, monkeypatch):
    db, user = context
    project = await create_project(ProjectCreate(template_id="anim_3d", source_type="script", source_text="完整旁白文案。",
                                                pipeline_mode="image_text", target_duration_sec=90), db, user)
    assert project.target_duration_sec == 90 and project.creation_session_id is None
    updated = await update_project(project.id, ProjectUpdate(target_duration_sec=30), db, user)
    assert updated.target_duration_sec == 30

    @asynccontextmanager
    async def local_session():
        yield db

    planner = AsyncMock(return_value=StoryboardResult(shots=[], character_bible="无人物"))
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", local_session)
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(chat_storyboard=planner))
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(pipeline, "_record_usage_est", AsyncMock())
    await pipeline._script_stage(project.id)
    extra = planner.await_args.kwargs["extra_requirements"]
    assert "30 秒" in extra and "不增加或删改旁白" in extra


@pytest.mark.parametrize("duration", [14, 181])
def test_invalid_target_duration_rejected(duration):
    with pytest.raises(ValidationError):
        ProjectCreate(template_id="anim_3d", source_text="测试主题", target_duration_sec=duration)
    with pytest.raises(ValidationError):
        ProjectUpdate(target_duration_sec=duration)
