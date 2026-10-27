"""任务提交后仍能返回剧本快照，重复请求不重复启动模型。"""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.drama.agents import router
from app.database import get_db
from app.deps import get_current_user
from app.models_drama import DramaProject, DramaScript
from app.models_tasks import TaskRun
from tests.conftest import make_user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,extra,status_key",
    [
        ("script_summary", {"episode_count": 3}, "summary_status"),
        ("episode_script", {}, "episode_content_status"),
        (
            "episode_script",
            {"episode_number": 1, "draft": "曹操迎汉献帝迁都许昌，面对诸侯的挑战，逐渐确立自己的政治地位。"},
            "episode_optimize_status",
        ),
    ],
)
async def test_submission_returns_script_and_repeated_request_does_not_launch_again(
    db_session, monkeypatch, endpoint, extra, status_key
):
    """走真实任务创建、权限锁和提交链路，隔离外部模型调用。"""
    launch = Mock()
    monkeypatch.setattr("app.services.tasks.direct.launch_direct_task", launch)
    monkeypatch.setattr(
        "app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=100)
    )
    user = await make_user(db_session)
    project = DramaProject(user_id=user.id, title="迁都许昌")
    db_session.add(project)
    await db_session.flush()
    script = DramaScript(
        project_id=project.id,
        name="迁都许昌",
        source="公元196年，曹操迎汉献帝迁都许昌，在动荡的时代中与各路诸侯展开博弈。",
        summary={"episodeCount": 3},
        params={},
    )
    db_session.add(script)
    await db_session.commit()
    project_id, script_id = project.id, script.id
    # 请求从数据库加载关系，避免构造时的对象引用掩盖异步懒加载问题。
    db_session.expunge(project)
    db_session.expunge(script)

    app = FastAPI()
    app.include_router(router, prefix="/api/drama")

    async def request_db():
        yield db_session

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        payload = {"project_id": project_id, **extra}
        response = await client.post(f"/api/drama/agents/{endpoint}", json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["ok"] is True
        assert result["queued"] is True
        assert result["status"] == "generating"
        assert result["task_id"] > 0
        assert result["script"]["id"] == script_id
        assert result["script"]["params"][status_key] == "generating"

        repeated = await client.post(f"/api/drama/agents/{endpoint}", json=payload)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["task_id"] is None
        assert repeated.json()["script"]["id"] == script_id

    tasks = list((await db_session.scalars(
        select(TaskRun).where(TaskRun.drama_project_id == project_id)
    )).all())
    assert [task.id for task in tasks] == [result["task_id"]]
    launch.assert_called_once()
    assert launch.call_args.args[0] == result["task_id"]
