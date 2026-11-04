"""回收后沿用真实视频轮询/下载链路，受控 HTTP 只允许原任务 GET。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models_drama import DramaAsset
from app.services.ark import ArkGateway
from app.services.tasks import executor, poller, scheduler
from tests.conftest import make_task
from tests.test_drama_fragment_save import make_episode
from tests.test_task_recovery_limit import interrupted, recovery_session


@pytest.mark.parametrize("kind", ["fragment_video", "asset_video"])
@pytest.mark.parametrize("source", ["column", "payload"])
async def test_recovered_provider_uses_existing_get_and_download_only(recovery_session, monkeypatch, kind, source):
    """真实 scheduler→poller 或 executor→asset handler→原下载函数，POST 始终为零。"""
    db = recovery_session
    user, project, ep, fragments = await make_episode(db)
    task = await make_task(db, user, domain="drama", task_type=kind, status="running")
    task.drama_project_id, task.episode_id = project.id, ep.id
    task.payload = {"prompt": "镜头"}
    if source == "column":
        task.provider_task_id = "existing-video"
    else:
        task.payload = {**task.payload, "provider_task_id": "existing-video"}
    if kind == "fragment_video":
        task.fragment_id = fragments[0].id
    else:
        asset = DramaAsset(project_id=project.id, name="视频", asset_type="video")
        db.add(asset)
        await db.flush()
        task.asset_id = asset.id
    await db.commit()
    await interrupted(db, task.id)
    assert await scheduler.recover_orphaned_tasks() == 1
    calls = []

    class Client:
        """在 HTTP 最外层记录方法；任何误提交都会进入 POST 断言。"""
        def __init__(self, **kwargs):
            """接受真实 gateway 的超时选项。"""
        async def __aenter__(self):
            """返回离线客户端。"""
            return self
        async def __aexit__(self, *args):
            """不持有外部资源。"""
        async def get(self, url, **kwargs):
            """按原任务返回在途或已生成状态。"""
            calls.append(("GET", url))
            return httpx.Response(200, json={"id": "existing-video",
                "status": "running" if kind == "fragment_video" else "succeeded",
                "content": {"video_url": "https://offline.test/video.mp4"}})
        async def post(self, url, **kwargs):
            """禁止恢复链路再次创建付费视频。"""
            calls.append(("POST", url))
            raise AssertionError("恢复不应发起新 POST")

    gateway = ArkGateway(SimpleNamespace(ark_base_url="https://www.tokenfree.com/v1", ark_api_key="offline"))
    monkeypatch.setattr(ArkGateway, "mock", property(lambda _: False))
    monkeypatch.setattr(gateway, "_ark_api_key", lambda: "offline")
    monkeypatch.setattr("app.services.ark.httpx.AsyncClient", Client)
    monkeypatch.setattr("app.services.tasks.upstream._persist_event", AsyncMock())
    monkeypatch.setattr("app.services.ark.get_ark", lambda: gateway)
    monkeypatch.setattr("app.services.drama.asset_video.get_ark", lambda: gateway)
    download = AsyncMock(return_value=("/static/recovered.mp4", None))
    monkeypatch.setattr(gateway, "save_video_assets_from_result", download)
    monkeypatch.setattr("app.services.drama.asset_video._publish_asset_video", AsyncMock(return_value="/static/recovered.mp4"))
    monkeypatch.setattr("app.services.drama.asset_video._bill_asset_video", AsyncMock())

    @asynccontextmanager
    async def session():
        """poller、执行器和业务 job 同用隔离事务。"""
        yield db
    monkeypatch.setattr(poller, "AsyncSessionLocal", session)
    monkeypatch.setattr(executor, "AsyncSessionLocal", session)
    monkeypatch.setattr("app.services.drama.jobs.AsyncSessionLocal", session)
    monkeypatch.setattr("app.database.AsyncSessionLocal", session)
    if kind == "fragment_video":
        await poller._select_and_poll_due()
        download.assert_not_awaited()
    else:
        await executor.execute_task_run(task.id)
        download.assert_awaited_once()
        await db.refresh(task)
        assert task.status == "succeeded"
    assert calls == [("GET", "https://www.tokenfree.com/v1/videos/existing-video")]
