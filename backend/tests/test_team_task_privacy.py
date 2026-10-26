"""Team task projections reveal coworker money only to the canonical team owner."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from app.api.tasks import public_task
from app.models import User
from app.models_teams import Team, TeamMember
from app.services.tasks.service import cancel_task_for_user


def task_row():
    return SimpleNamespace(id=1, domain="drama", task_type="asset_image", status="running", priority=100, requested_by=2, team_id=91, billing_estimate_fen=300, billing_charged_fen=180, billing_refunded_fen=120, events=[SimpleNamespace(id=1, event_type="task.completed", message="扣费 ¥1.80", payload={"amount_fen": 180, "result": {"cost": 90, "video_url": "/video.mp4"}, "items": [{"refund_yuan": 1.2, "label": "成功"}]})], steps=[], targets=[])


@pytest.mark.parametrize("viewer_id,role,visible", [(1, "member", True), (2, "member", True), (3, "admin", False), (4, "member", False)])
def test_team_task_money_is_owner_or_actor_only(viewer_id, role, visible):
    original = task_row()
    output = public_task(original, viewer=User(id=viewer_id, role=role), owner_user_id=1)
    assert output.billing_estimate_fen == (300 if visible else None)
    assert output.billing_charged_fen == (180 if visible else None)
    assert output.billing_refunded_fen == (120 if visible else None)
    if visible:
        assert output.events[0].payload["amount_fen"] == 180
    else:
        assert output.events[0].payload == {"result": {"video_url": "/video.mp4"}, "items": [{"label": "成功"}]}
        assert "1.80" not in output.events[0].message
    assert original.events[0].payload["amount_fen"] == 180


@pytest.mark.asyncio
async def test_cancel_uses_one_team_authorization_with_request_context(monkeypatch):
    task = SimpleNamespace(id=1, domain="drama", task_type="asset_image", payload={}, status="running", requested_by=2, team_id=91, cancelable=True, current_step_key="image")
    result = SimpleNamespace(scalar_one_or_none=lambda: task)
    db = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), commit=AsyncMock())
    access = AsyncMock(return_value=(Team(id=91, owner_user_id=1), TeamMember(team_id=91, user_id=3, role="admin", active=True)))
    monkeypatch.setattr("app.services.teams.access.get_team", access)
    context = {}
    await cancel_task_for_user(db, User(id=3), 1, access_context=context)
    access.assert_awaited_once()
    assert context[(91, 3)][0].owner_user_id == 1
