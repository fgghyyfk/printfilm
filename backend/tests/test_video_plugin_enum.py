"""MiniMax 多插件枚举兼容，拒绝响应才允许重试。"""
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.tokenfree_video import post_video_create


@pytest.mark.parametrize("status,code,retries", [(400, "plugin_usage_invalid", 2), (500, "plugin_usage_invalid", 1), (400, "other", 1), (200, "", 1)])
async def test_plugin_enum_retry(monkeypatch, status, code, retries):
    """只对明确拒绝的枚举错误重试，不修改原任务快照。"""
    body = {"model": "MiniMax-H3", "seconds": "13", "metadata": {"resolution": "480p", "ratio": "9:16"}}
    first = httpx.Response(status, json={"code": code})
    accepted = httpx.Response(200, json={"task_id": "ok"})
    request = AsyncMock(side_effect=[first, accepted])
    monkeypatch.setattr("app.services.tasks.upstream.observed_request", request)
    await post_video_create(None, "POST", "https://example.test/videos", json=body)
    assert request.await_count == retries
    assert body["metadata"]["resolution"] == "480p"
    if retries == 2:
        assert request.await_args_list[1].kwargs["json"]["metadata"] == {"resolution": "480P", "ratio": "9:16"}


async def test_plugin_enum_retry_is_bounded(monkeypatch):
    """另一插件也拒绝时停止，不能无限翻转或改档。"""
    request = AsyncMock(return_value=httpx.Response(400, json={"code": "plugin_usage_invalid"}))
    monkeypatch.setattr("app.services.tasks.upstream.observed_request", request)
    response = await post_video_create(None, "POST", "https://example.test/videos", json={"model": "MiniMax-H3", "metadata": {"resolution": "768P"}})
    assert response.status_code == 400
    assert request.await_count == 2
    assert request.await_args.kwargs["json"]["metadata"]["resolution"] == "768p"


@pytest.mark.parametrize("model,resolution,error", [
    ("seedance-2.0", "480p", {"code": "plugin_usage_invalid"}),
    ("MiniMax-H3", "1080p", {"code": "plugin_usage_invalid"}),
    ("MiniMax-H3", "480p", {"code": "plugin_usage_invalid", "task_id": "accepted"}),
])
async def test_other_models_and_accepted_tasks_not_retried(monkeypatch, model, resolution, error):
    """不改变模型、画质档位或重复提交已经有任务号的请求。"""
    request = AsyncMock(return_value=httpx.Response(400, json=error))
    monkeypatch.setattr("app.services.tasks.upstream.observed_request", request)
    await post_video_create(None, "POST", "https://example.test/videos", json={"model": model, "metadata": {"resolution": resolution}})
    assert request.await_count == 1


async def test_timeout_not_retried(monkeypatch):
    """提交结果不明确时禁止自动重发。"""
    request = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
    monkeypatch.setattr("app.services.tasks.upstream.observed_request", request)
    with pytest.raises(httpx.ReadTimeout):
        await post_video_create(None, "POST", "https://example.test/videos", json={"model": "MiniMax-H3", "metadata": {"resolution": "480p"}})
    assert request.await_count == 1
