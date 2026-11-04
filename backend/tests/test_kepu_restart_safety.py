"""重做与合成的稿件/文件保护边界。"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import projects
from app.models import Shot
from app.schemas import ShotUpdate
from app.services.kepu_shot_edits import clear_storyboard_rows
from tests.test_kepu_task_recovery import seed_project
from tests.conftest import make_task


async def test_restart_keeps_old_manuscript_and_files_until_execution(db_session, tmp_path, monkeypatch):
    user, project, shot = await seed_project(db_session)
    old_final = project.final_video_url
    old = tmp_path / "full_narration.mp3"
    old.write_bytes(b"old published audio")
    monkeypatch.setattr("app.services.storage.project_dir", lambda _: tmp_path)
    with patch("app.services.billing.settlement.ensure_balance_for_task", AsyncMock(return_value=0)):
        result = await projects.generate_project(project.id, True, db_session, user)
    assert result.final_video_url == old_final
    assert [s.id for s in result.shots] == [shot.id]
    assert old.read_bytes() == b"old published audio"
    assert result.active_tasks[0].payload["restart"] is True


async def test_new_manuscript_detaches_history_without_deleting_media(db_session, tmp_path):
    user, project, shot = await seed_project(db_session)
    task = await make_task(db_session, user=user, domain="kepu", task_type="shot_regen_image", status="succeeded")
    task.shot_id = shot.id
    await db_session.commit()
    old = tmp_path / "old.mp4"
    old.write_bytes(b"published")
    await db_session.refresh(project, ["shots"])
    await clear_storyboard_rows(db_session, project)
    await db_session.commit()
    assert await db_session.get(Shot, shot.id) is None
    await db_session.refresh(task)
    assert task.shot_id is None
    assert old.read_bytes() == b"published"


@pytest.mark.parametrize("status", ["pending", "running", "cancel_requested"])
async def test_edit_rejects_live_compose_before_text_or_file_changes(db_session, tmp_path, monkeypatch, status):
    user, project, shot = await seed_project(db_session)
    task = await make_task(db_session, user=user, domain="kepu", task_type="project_compose_only", status=status)
    task.project_id = project.id
    await db_session.commit()
    old = tmp_path / "full_narration.mp3"
    old.write_bytes(b"old audio")
    monkeypatch.setattr("app.services.storage.project_dir", lambda _: tmp_path)
    with pytest.raises(HTTPException) as exc:
        await projects.update_shot(project.id, shot.id, ShotUpdate(narration="new"), db_session, user)
    assert exc.value.status_code == 409
    assert shot.narration == "测试"
    assert old.read_bytes() == b"old audio"
