"""上游真实请求留痕：脱敏、单次提交、轮询限流与独立短事务，全部离线。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.models_tasks import TaskRun, TaskStep
from app.services.billing.context import billing_scope
from app.services.tasks import upstream
from app.services.upstream_sanitize import sanitize_upstream_value


def test_sanitizer_redacts_credentials_and_signed_urls_without_mutation():
    """嵌套凭据、签名 URL、内嵌错误文本都不落密钥；计费 token 数保留。"""
    raw = {
        "headers": {"Authorization": "Bearer private-key", "X-Api-Key": "private-api", "Cookie": "sid=private-cookie"},
        "body": {"access_token": "private-token", "max_tokens": 8000, "nested": {"clientSecret": "private-secret"}},
        "image": "https://user:private-password@cdn.test/a.png?X-Tos-Signature=private-sign&width=100",
        "error": "request https://cdn.test/a?api_key=private-query failed; Authorization: Bearer private-error",
        "raw_error": '{"api_key":"private-json","password": "private-json-pass"}',
    }
    original = json.dumps(raw)
    safe = sanitize_upstream_value(raw)
    serialized = json.dumps(safe)
    for secret in ("private-key", "private-api", "private-cookie", "private-token", "private-secret", "private-password", "private-sign", "private-query", "private-error", "private-json", "private-json-pass"):
        assert secret not in serialized
    assert safe["body"]["max_tokens"] == 8000
    assert "width=100" in safe["image"]
    assert json.dumps(raw) == original


def test_sanitizer_summarizes_base64_and_binary_instead_of_saving_content():
    """媒体内容只保留类型和长度，避免图像/音频正文进入任务事件。"""
    safe = sanitize_upstream_value({
        "image": "data:image/png;base64,cHJpdmF0ZS1pbWFnZQ==",
        "b64_json": "YWJj",
        "binary": b"private-bytes",
    })
    assert safe["image"]["media_type"] == "image/png"
    assert safe["image"]["length"] == 20
    assert safe["b64_json"] == {"type": "base64", "length": 4}
    assert safe["binary"] == {"type": "bytes", "length": 13}
    assert "cHJpdmF0ZS1pbWFnZQ" not in json.dumps(safe)


@pytest.mark.parametrize("text,secret", [
    ('{"api_key": "abcdef-secret"}', "abcdef-secret"),
    ('password="foo"', "foo"),
    ("password='a secret with spaces'", "a secret with spaces"),
    ('{"client_secret":"unprefixed-credential"}', "unprefixed-credential"),
    ("API Key: arbitrary-credential", "arbitrary-credential"),
])
def test_free_text_json_and_quoted_credentials_are_redacted(text, secret):
    """上游错误回显中的引号/JSON凭据也必须脱敏，不能依赖 sk- 前缀。"""
    assert secret not in json.dumps(sanitize_upstream_value({"error": {"message": text}}))


@pytest.fixture(autouse=True)
def reset_poll_cache():
    """隔离轮询去重状态。"""
    upstream._poll_cache.clear()
    yield
    upstream._poll_cache.clear()


async def test_multipart_request_records_attachment_metadata_without_media_bytes(monkeypatch):
    """实际 multipart 请求保留文件名/媒体类型，附件正文仅留长度。"""
    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    response = httpx.Response(200, json={"data": []})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    files = {"image": ("reference.png", b"private-image-content", "image/png")}
    async with billing_scope(42):
        await upstream.observed_request(client, "POST", "https://gateway.test/v1/images/edits", data={"model": "gpt-image-2"}, files=files)
    client.post.assert_awaited_once_with("https://gateway.test/v1/images/edits", data={"model": "gpt-image-2"}, files=files)
    recorded = persist.await_args_list[0].args[2]["request"]
    assert recorded["files"]["image"] == ["reference.png", {"type": "bytes", "length": 21}, "image/png"]
    assert "private-image-content" not in json.dumps(recorded)


@pytest.mark.asyncio
async def test_submit_records_actual_body_and_response_and_preserves_network_call(monkeypatch):
    """日志来自实际 HTTP 参数；请求成功只执行一次且记录接收的上游 ID。"""
    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    response = httpx.Response(200, json={"task_id": "provider-123", "status": "queued"}, headers={"x-request-id": "trace-1"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    body = {"model": "MiniMax-H3", "prompt": "松鼠捧起松果", "seconds": "6"}
    headers = {"Authorization": "Bearer private-key", "Content-Type": "application/json"}
    async with billing_scope(42):
        actual = await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", headers=headers, json=body)
    assert actual is response
    client.post.assert_awaited_once_with("https://gateway.test/v1/videos", headers=headers, json=body)
    calls = persist.await_args_list
    assert [call.args[1] for call in calls] == ["upstream.request.started", "upstream.request.accepted"]
    started, accepted = [call.args[2] for call in calls]
    assert started["request"]["body"] == body
    assert started["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert accepted["provider_task_id"] == "provider-123"
    assert accepted["response"]["status_code"] == 200
    assert accepted["status_code"] == 200
    assert accepted["elapsed_ms"] >= 0
    assert started["request_id"] == accepted["request_id"]


@pytest.mark.asyncio
async def test_logging_failure_cannot_repeat_successful_request(monkeypatch):
    """记录器故障不允许冒泡到业务重试路径。"""
    monkeypatch.setattr(upstream, "_persist_event", AsyncMock(side_effect=RuntimeError("log database offline")))
    response = httpx.Response(200, json={"id": "accepted-1"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    async with billing_scope(42):
        assert await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={"model": "seedance-2.0"}) is response
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_logging_timeout_is_bounded_and_keeps_request_success(monkeypatch):
    """事件写入锁等待只消耗短预算，不阻塞真实请求完成。"""
    async def blocked_write(*_args, **_kwargs):
        """模拟事件事务等待锁。"""
        await asyncio.Event().wait()

    monkeypatch.setattr(upstream, "_persist_event", blocked_write)
    monkeypatch.setattr(upstream, "_EVENT_TIMEOUT_SEC", 0.001)
    response = httpx.Response(200, json={"id": "accepted-1"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    async with billing_scope(42):
        assert await asyncio.wait_for(upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={}), timeout=1) is response
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_network_error_records_failed_event_and_keeps_original_exception(monkeypatch):
    """网络异常仍由原业务处理，留痕中不得保留错误 URL 的签名。"""
    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    error = httpx.ReadTimeout("read timeout https://gateway.test/v1/videos?token=private-token")
    client = SimpleNamespace(post=AsyncMock(side_effect=error))
    async with billing_scope(42):
        with pytest.raises(httpx.ReadTimeout) as caught:
            await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={"model": "seedance-2.0"})
    assert caught.value is error
    failed = persist.await_args_list[-1].args[2]
    assert persist.await_args_list[-1].args[1] == "upstream.request.failed"
    assert "private-token" not in json.dumps(failed)


@pytest.mark.asyncio
async def test_poll_records_status_changes_and_http_code_without_each_tick_events(monkeypatch):
    """持续 running 不刷屏，状态变为 succeeded 时保留新的响应。"""
    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    responses = [httpx.Response(200, json={"id": "provider-1", "status": status}) for status in ["running"] * 5 + ["succeeded"]]
    client = SimpleNamespace(get=AsyncMock(side_effect=responses))
    async with billing_scope(42):
        for _ in responses:
            await upstream.observed_request(client, "GET", "https://gateway.test/v1/videos/provider-1")
    assert client.get.await_count == 6
    assert persist.await_count == 2
    assert {call.args[1] for call in persist.await_args_list} == {"upstream.request.poll"}
    assert [call.args[2]["response"]["status_code"] for call in persist.await_args_list] == [200, 200]


@pytest.mark.asyncio
async def test_without_task_context_does_not_open_logging_transaction(monkeypatch):
    """非任务请求不触发日志数据库 I/O。"""
    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    response = httpx.Response(200, json={"id": "provider-1"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    async with billing_scope(None):
        assert await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={}) is response
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncommitted_inline_task_records_to_caller_without_query_flush_or_commit(monkeypatch):
    """未提交 TaskRun 的事件沿调用者事务保存，日志不能另开连接或提交。"""
    task = TaskRun(id=42, status="running", current_step_key="submit")
    step = TaskStep(id=7, task_id=42, step_key="submit", step_type="job")
    events = []
    db = SimpleNamespace(identity_map={}, new={task, step}, add=events.append,
                         commit=AsyncMock(), flush=AsyncMock(), execute=AsyncMock(), get=AsyncMock())
    independent_session = Mock(side_effect=AssertionError("must not open another session"))
    monkeypatch.setattr(upstream, "AsyncSessionLocal", independent_session)
    response = httpx.Response(200, json={"task_id": "inline-provider"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    async with billing_scope(42), upstream.upstream_event_session(db):
        assert await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={"model": "MiniMax-H3"}) is response
    assert [event.event_type for event in events] == ["upstream.request.started", "upstream.request.accepted"]
    assert task.provider_task_id == step.provider_task_id == "inline-provider"
    assert task.status == "running"
    for operation in (db.commit, db.flush, db.execute, db.get):
        operation.assert_not_awaited()
    independent_session.assert_not_called()
    assert upstream._event_session.get() is None


@pytest.mark.asyncio
async def test_caller_session_logging_error_does_not_repeat_network_request(monkeypatch):
    """调用者 session 的日志 add 异常不能让已成功请求重试。"""
    db = SimpleNamespace(identity_map={}, new=set(), add=Mock(side_effect=RuntimeError("event unavailable")),
                         commit=AsyncMock(), flush=AsyncMock())
    response = httpx.Response(200, json={"task_id": "inline-provider"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    async with billing_scope(42), upstream.upstream_event_session(db):
        assert await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={}) is response
    client.post.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_ephemeral_executor_installs_caller_event_session(monkeypatch):
    """同步和异步任务先登记受理；请求日志共用执行会话且不额外提交。"""
    from app.services.billing import ephemeral

    task = TaskRun(id=42, status="pending", current_step_key="submit")
    events = []
    db = SimpleNamespace(identity_map={42: task}, new=set(), add=events.append,
                         flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(ephemeral, "create_ephemeral_task_row", AsyncMock(return_value=task))
    monkeypatch.setattr(ephemeral, "freeze_for_task", AsyncMock())
    monkeypatch.setattr(ephemeral, "settle_task", AsyncMock())
    monkeypatch.setattr(ephemeral, "append_task_event", AsyncMock())
    response = httpx.Response(200, json={"task_id": "inline-provider"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))

    async def executor():
        """发出唯一实际请求，检查事务上下文已正确传播。"""
        assert upstream._event_session.get() is db
        await upstream.observed_request(client, "POST", "https://gateway.test/v1/videos", json={"model": "MiniMax-H3"})
        return {"task_id": "inline-provider"}

    run = ephemeral.run_billed_ephemeral
    await run(db, SimpleNamespace(id=9), domain="studio", task_type="tool_video", executor=executor, commit=False)
    assert [event.event_type for event in events] == ["upstream.request.started", "upstream.request.accepted"]
    assert db.flush.await_count == 1  # 仅业务已有的 task.started flush。
    db.commit.assert_awaited_once()  # 接受任务在模型调用前持久化。
    assert upstream._event_session.get() is None


@pytest.mark.asyncio
async def test_accepted_video_id_is_written_to_current_step_without_state_change(monkeypatch):
    """补齐任务与步骤上游 ID，但不能把同步资产视频任务改成 awaiting_poll。"""
    task = TaskRun(id=42, domain="drama", task_type="asset_video", status="running", current_step_key="asset_video")
    step = TaskStep(id=7, task_id=42, step_key="asset_video", step_type="job", status="submitting")
    events = []

    class Session:
        """模拟独立短事务。"""

        async def __aenter__(self):
            """进入内存 session。"""
            return self

        async def __aexit__(self, *_args):
            """释放内存 session。"""
            return False

        async def get(self, _model, _task_id):
            """返回任务替身。"""
            return task

        async def execute(self, _statement):
            """返回当前步骤。"""
            return SimpleNamespace(scalar_one_or_none=lambda: step)

        def add(self, event):
            """记录追加的事件。"""
            events.append(event)

        async def commit(self):
            """模拟独立提交。"""
            return None

    monkeypatch.setattr(upstream, "AsyncSessionLocal", Session)
    payload = {"provider_task_id": "provider-1", "provider": "tokenfree", "is_video_submit": True}
    await upstream._persist_event(42, "upstream.request.accepted", payload)
    assert task.provider_task_id == step.provider_task_id == "provider-1"
    assert step.provider_name == "tokenfree"
    assert task.status == "running"
    assert step.status == "submitting"
    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["MiniMax-H3", "seedance-2.0"])
async def test_actual_ark_video_submit_records_gateway_body_and_provider_id(monkeypatch, model):
    """覆盖真实 MiniMax/Seedance POST 入口，记录转换后的网关 body 而非任务参数。"""
    from app.services import ark as ark_module

    persist = AsyncMock()
    monkeypatch.setattr(upstream, "_persist_event", persist)
    response = httpx.Response(200, json={"task_id": f"provider-{model}", "status": "queued"})
    post = AsyncMock(return_value=response)

    class Client:
        """模拟 ArkGateway 使用的 HTTP 客户端。"""

        def __init__(self, **_kwargs):
            """接收原超时配置。"""
            self.post = post

        async def __aenter__(self):
            """进入 HTTP 上下文。"""
            return self

        async def __aexit__(self, *_args):
            """结束 HTTP 上下文。"""
            return False

    gateway = ark_module.ArkGateway(SimpleNamespace(ark_base_url="https://www.tokenfree.com/v1"))
    monkeypatch.setattr(ark_module.ArkGateway, "mock", property(lambda _self: False))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *_args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *_args: {"Authorization": "Bearer private-key"})
    monkeypatch.setattr(gateway, "_seedance_duration", lambda value, **_kwargs: int(value))
    body = {"model": model, "duration": 6, "content": [{"type": "text", "text": "真实视频提示词"}]}
    monkeypatch.setattr(gateway, "_resolve_seedance_content_items", AsyncMock(return_value=body["content"]))
    monkeypatch.setattr("app.services.drama.voice_reference_audio.validate_seedance_body_reference_audios", AsyncMock())
    monkeypatch.setattr(ark_module.httpx, "AsyncClient", Client)
    async with billing_scope(42):
        provider_id = await gateway.gen_video_seedance_body(body)
    assert provider_id == f"provider-{model}"
    post.assert_awaited_once()
    actual_body = post.await_args.kwargs["json"]
    assert actual_body["prompt"] == "真实视频提示词"
    assert actual_body["seconds"] == "6"
    assert "content" not in actual_body
    events = persist.await_args_list
    assert events[0].args[2]["request"]["body"] == actual_body
    assert events[-1].args[2]["provider_task_id"] == provider_id
