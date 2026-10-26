"""Legacy endpoints cannot create an uncoordinated second producer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import projects
from app.schemas import ProjectUpdate, ShotUpdate


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,kwargs", [
    (projects.generate_project, {}),
    (projects.cancel_project, {}),
    (projects.compose_only, {}),
    (projects.regen_all_audio, {}),
    (projects.regen_image, {"shot_id": 1}),
    (projects.regen_video, {"shot_id": 1}),
    (projects.regen_audio, {"shot_id": 1}),
    (projects.update_project, {"body": ProjectUpdate(title="绕过会话")}),
    (projects.update_shot, {"shot_id": 1, "body": ShotUpdate(narration="绕过会话")}),
])
async def test_agent_project_rejects_legacy_mutation(monkeypatch, endpoint, kwargs):
    project = SimpleNamespace(id=1, creation_session_id=91, status="SCRIPT_READY", active_tasks=[])
    monkeypatch.setattr(projects, "_get_owned_project", AsyncMock(return_value=project))
    db = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await endpoint(project_id=1, db=db, user=SimpleNamespace(id=1), **kwargs)
    assert error.value.status_code == 409
    assert "对话" in error.value.detail
    db.commit.assert_not_awaited()
    db.execute.assert_not_awaited()


def test_legacy_project_remains_editable():
    projects._ensure_wizard_project(SimpleNamespace(creation_session_id=None))
    projects._ensure_side_task_allowed(SimpleNamespace(active_tasks=[]))
