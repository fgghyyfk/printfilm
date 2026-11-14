"""The editor's prompt optimization keeps its real project billing scope."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.models import User
from app.schemas_agent import AgentSkillOptimizeBody
from app.api.drama import skills


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [None, 71])
async def test_skill_prompt_optimization_preserves_project_scope(monkeypatch, project_id):
    optimize = AsyncMock(return_value="优化后的提示词")
    usage = AsyncMock()
    monkeypatch.setattr(skills, "optimize_prompt_with_skills", optimize)
    monkeypatch.setattr(skills, "record_llm_chat_line", usage)
    async def billed(db, user, **kwargs):
        assert kwargs["drama_project_id"] == project_id
        assert kwargs["task_type"] == "skill_optimize"
        return SimpleNamespace(id=9), await kwargs["executor"]()
    monkeypatch.setattr(skills, "run_billed_ephemeral", billed)
    result = await skills.optimize_prompt(AgentSkillOptimizeBody(prompt="画面提示", skill_ids=[1], project_id=project_id), db=SimpleNamespace(), user=User(id=2))
    assert result.task_id == 9
    assert usage.await_args.kwargs["drama_project_id"] == project_id


def test_skill_optimization_rejects_invalid_project_id():
    with pytest.raises(ValidationError):
        AgentSkillOptimizeBody(prompt="test", project_id=0)
