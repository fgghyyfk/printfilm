"""TTS 按真实成功响应记账，不随调用入口重复或遗漏成本。"""
from __future__ import annotations

import base64
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from tests.tts_database import db_session
from unittest.mock import AsyncMock
from sqlalchemy import select, text

from app.config import get_settings
from app.models import Project, Template, UsageEvent
from app.services import ark, kepu_avatar_billing, pipeline, volc_tts
from app.services.billing.context import billing_scope
from app.services.tasks.upstream import upstream_event_session
from tests.conftest import make_task, make_user


async def _kepu_task(db):
    """建立普通科普流水线任务，保留真实外键和数据库记账。"""
    user = await make_user(db)
    template = Template(id=f"tts-{uuid.uuid4().hex[:10]}", name="配音模板", style_prefix="x")
    db.add(template)
    project = Project(user_id=user.id, template_id=template.id, source_text="测试", title="配音测试")
    db.add(project)
    await db.flush()
    task = await make_task(db, user, domain="kepu", task_type="project_pipeline")
    task.project_id = project.id
    await db.flush()
    await db.commit()
    return user, project, task


def _gateway(monkeypatch, tmp_path):
    """隔离所有上游：只允许测试注入的 HTTP 传输与本地音频。"""
    # 旧价目测试会直接修改缓存 Settings；本组费用断言固定汇率，避免依赖执行顺序。
    monkeypatch.setattr(get_settings(), "billing_usd_cny", 7.2)
    settings = get_settings().model_copy(update={
        "ark_mock": False, "ark_api_key": "test-tokenfree", "ark_base_url": "https://tokenfree.test/v1",
        "volc_tts_api_key": "test-volc", "volc_tts_base_url": "https://volc.test",
        "volc_tts_fen_per_10k_chars": 500,
    })
    monkeypatch.setattr(volc_tts, "get_settings", lambda: settings)
    monkeypatch.setattr(kepu_avatar_billing, "get_settings", lambda: settings)
    monkeypatch.setattr(ark.storage, "project_dir", lambda pid: tmp_path / str(pid))
    monkeypatch.setattr(ark.storage, "publish_local", lambda path: str(path))
    monkeypatch.setattr(ark.storage, "local_path_from_url", lambda url: Path(url))
    monkeypatch.setattr(ark, "is_near_silent_audio", lambda path: False)
    monkeypatch.setattr(pipeline, "is_near_silent_audio", lambda path: False)
    monkeypatch.setattr(pipeline, "probe_duration", lambda path: 4.0)
    return ark.ArkGateway(settings=settings)


def _http(monkeypatch, handler):
    """让全部 HTTP 请求走确定性的内存响应。"""
    client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))


def _volc_audio():
    """返回有效长度的合成音频流，无真实联网。"""
    return httpx.Response(200, text="\n".join(json.dumps(item) for item in [
        {"code": 0, "data": base64.b64encode(b"ID3" + b"a" * 4000).decode()},
        {"code": 20000000},
    ]))


def _tokenfree_audio(model="qwen3-omni-flash", *, usage=None):
    """返回带音频及实际 usage 的 Omni SSE 或普通 chat 响应。"""
    audio = base64.b64encode(b"ID3" + b"t" * 4000).decode()
    usage = usage if usage is not None else {"total_tokens": 100, "quota_consumed": 5000}
    if "omni" in model:
        return httpx.Response(200, text="\n".join([
            "data: " + json.dumps({"choices": [{"delta": {"audio": {"data": audio}}}]}),
            "data: " + json.dumps({"usage": usage}), "data: [DONE]",
        ]))
    return httpx.Response(200, json={
        "choices": [{"message": {"audio": {"data": audio}}}], "usage": usage,
    })


def _edge(monkeypatch, gateway, *, fail=False):
    """注入免费的本地 edge 输出或失败，禁止真正联网。"""
    async def synthesize(text, dest, voice_hint=""):
        """写入最终兜底音频。"""
        if fail:
            raise RuntimeError("edge down")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"ID3" + b"e" * 4000)
    monkeypatch.setattr(gateway, "_tts_edge", synthesize)


async def _events(db, task):
    """读取当前任务真实数据库用量行。"""
    return list((await db.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id)
                                  .order_by(UsageEvent.id))).all())


async def test_ordinary_kepu_continuous_volc_records_once(db_session, monkeypatch, tmp_path):
    """普通非数字人 TaskRun 的豆包整片配音仅记实际的一次成本。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    monkeypatch.setattr(pipeline, "get_ark", lambda: gateway)

    @asynccontextmanager
    async def session():
        """用测试外层回滚事务承接生产代码的真实数据库写入。"""
        yield db_session

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    async with billing_scope(task.id):
        await pipeline._synthesize_continuous_audio(
            project.id, voice="narrator_calm",
            shot_rows=[SimpleNamespace(id=0, narration="你好世界", duration=4.0)], force=True,
        )
    rows = await _events(db_session, task)
    assert len(rows) == 1, [(row.billing_key, row.provider, row.cost_fen) for row in rows]


@pytest.mark.parametrize("final_provider", ["tokenfree", "edge"])
async def test_volc_failure_records_only_actual_fallback(db_session, monkeypatch, tmp_path, final_provider):
    """配置了火山但请求失败，仅实际成功的付费 provider 产生费用。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _edge(monkeypatch, gateway)

    def response(request):
        """火山失败，按用例控制 TokenFree 是否成功。"""
        if request.url.host == "volc.test" or final_provider == "edge":
            return httpx.Response(503)
        return _tokenfree_audio()

    _http(monkeypatch, response)
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == final_provider
    assert result.is_fallback is True
    assert result.usage_recorded is (final_provider == "tokenfree")
    assert len(rows) == (1 if final_provider == "tokenfree" else 0)
    if rows:
        assert rows[0].provider == result.provider == "tokenfree"
        assert rows[0].model == result.model == "qwen3-omni-flash"
        assert rows[0].total_tokens == 100
        assert rows[0].cost_fen == 8  # 5000 quota → 0.01 USD → 向上取整人民币分
        assert rows[0].estimated is False
        raw = json.loads(rows[0].raw_usage_json)
        assert raw["usage"]["quota_consumed"] == 5000
        assert "audio" not in rows[0].raw_usage_json


@pytest.mark.parametrize("final", ["tokenfree", "edge", "fail"])
async def test_successful_but_silent_paid_response_keeps_cost(db_session, monkeypatch, tmp_path, final):
    """火山音频真实成功但静音时，保留其费用并记录后续实际成功成本。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _edge(monkeypatch, gateway, fail=final == "fail")
    _http(monkeypatch, lambda request: _volc_audio() if request.url.host == "volc.test" else (
        _tokenfree_audio() if final == "tokenfree" else httpx.Response(503)))
    monkeypatch.setattr(ark, "is_near_silent_audio", lambda path: Path(path).read_bytes()[3:4] == b"a")
    async with billing_scope(task.id), upstream_event_session(db_session):
        if final == "fail":
            with pytest.raises(RuntimeError, match="配音失败"):
                await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
        else:
            result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
            assert result.provider == final
            assert result.is_fallback is True
    rows = await _events(db_session, task)
    assert [row.provider for row in rows] == (["volc", "tokenfree"] if final == "tokenfree" else ["volc"])
    assert rows[0].cost_fen == 1


async def test_tokenfree_cost_survives_local_conversion_failure(db_session, monkeypatch, tmp_path):
    """TokenFree 返回真实音频后本地 FFmpeg 失败，费用仍保留，edge 不收费。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _edge(monkeypatch, gateway)
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    monkeypatch.setattr(ark, "iter_tokenfree_tts_models", lambda model: ["qwen3-omni-flash"])
    monkeypatch.setattr(gateway, "_persist_tts_mp3", lambda dest, audio: False)
    _http(monkeypatch, lambda request: _tokenfree_audio())
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == "edge"
    assert result.usage_recorded is False
    assert [(row.provider, row.model, row.cost_fen) for row in rows] == [("tokenfree", "qwen3-omni-flash", 8)]


async def test_unscoped_preview_does_not_create_paid_usage(db_session, monkeypatch, tmp_path):
    """没有 TaskRun 范围的试听可返回音频，但不能制造无法结算的付费行。"""
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    result = await gateway.tts_with_result("试听", "narrator_calm")
    assert result.provider == "volc"
    assert result.usage_recorded is False


async def test_no_key_edge_is_free_and_keeps_url_compatibility(db_session, monkeypatch, tmp_path):
    """没有付费凭据时不按 TokenFree 估价，旧 tts 接口仍返回 URL。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    gateway.settings.ark_api_key = ""
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    _edge(monkeypatch, gateway)
    _http(monkeypatch, lambda request: pytest.fail("无 key 不应请求付费接口"))
    async with billing_scope(task.id):
        url = await gateway.tts("你好", "narrator_calm", project_id=project.id)
    assert isinstance(url, str)
    assert Path(url).exists()
    assert await _events(db_session, task) == []


async def test_concurrent_calls_keep_provider_model_and_task_local(db_session, monkeypatch, tmp_path):
    """同一 gateway 的两个 TaskRun 越过响应屏障，元数据及费用不能串任务。"""
    _, project_a, task_a = await _kepu_task(db_session)
    _, project_b, task_b = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    entered = 0
    barrier = asyncio.Event()

    async def response(request):
        """两个火山请求都到达后，A 成功、B 失败并继续 TokenFree。"""
        nonlocal entered
        if request.url.host == "volc.test":
            entered += 1
            if entered == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 2)
            return _volc_audio() if "并发甲" in request.content.decode() else httpx.Response(503)
        return _tokenfree_audio()
    _http(monkeypatch, response)

    async def run(project, task, text):
        """每个并发协程独立绑定计费和上游事件范围。"""
        async with billing_scope(task.id), upstream_event_session(db_session):
            return await gateway.tts_with_result(text, "narrator_calm", project_id=project.id)
    first, second = await asyncio.gather(run(project_a, task_a, "并发甲"), run(project_b, task_b, "并发乙"))
    assert (first.provider, first.is_fallback, first.usage_recorded) == ("volc", False, True)
    assert (second.provider, second.model, second.is_fallback, second.usage_recorded) == (
        "tokenfree", "qwen3-omni-flash", True, True)
    rows_a, rows_b = await _events(db_session, task_a), await _events(db_session, task_b)
    assert [(row.provider, row.project_id) for row in rows_a] == [("volc", project_a.id)]
    assert [(row.provider, row.project_id) for row in rows_b] == [("tokenfree", project_b.id)]


async def test_database_record_failure_does_not_retry_paid_synthesis(db_session, monkeypatch, tmp_path):
    """记账 SQL 暂时失败仅重试本地保存点，不能再次调用真实付费合成。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    seen = []

    def response(request):
        """只允许一次火山音频响应。"""
        seen.append(request.url.host)
        assert request.url.host == "volc.test"
        return _volc_audio()
    _http(monkeypatch, response)
    from app.services import billing
    record_line = billing.record_line
    attempts = 0

    async def broken_record(db, **kwargs):
        """首次真实 SQL 错误，外层事务可继续并完成本地第二次落账。"""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await db.execute(text("SELECT 1 / 0"))
        return await record_line(db, **kwargs)
    monkeypatch.setattr(billing, "record_line", broken_record)
    async with billing_scope(task.id):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    assert seen == ["volc.test"]
    assert result.provider == "volc"
    assert result.usage_recorded is True
    assert attempts == 2
    assert await db_session.scalar(text("SELECT 1")) == 1
    assert len(await _events(db_session, task)) == 1


async def test_continuous_tokenfree_preserves_estimate_per_shot(db_session, monkeypatch, tmp_path):
    """TokenFree 无 usage 时仍使用原整片镜数乘以默认估量。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    monkeypatch.setattr(pipeline, "get_ark", lambda: gateway)
    _http(monkeypatch, lambda request: _tokenfree_audio(usage={}))

    @asynccontextmanager
    async def session():
        """继续使用真实测试库，只隔离生产连接地址。"""
        yield db_session
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    async with billing_scope(task.id), upstream_event_session(db_session):
        await pipeline._synthesize_continuous_audio(
            project.id, voice="narrator_calm", shot_rows=[
                SimpleNamespace(id=0, narration=f"第{i}镜", duration=4.0) for i in range(3)
            ], force=True)
    rows = await _events(db_session, task)
    assert len(rows) == 1
    assert rows[0].provider == "tokenfree" and rows[0].model == "qwen3-omni-flash"
    assert rows[0].total_tokens == get_settings().billing_est_tts_tokens * 3
    assert rows[0].estimated is True


async def test_two_successful_tokenfree_responses_keep_both_costs(db_session, monkeypatch, tmp_path):
    """首个付费音频本地处理失败后，备用付费模型成功，保留两次实际成本。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    monkeypatch.setattr(ark, "iter_tokenfree_tts_models", lambda model: ["qwen-tts-2025-05-22", "gemini-3.1-flash-tts"])
    _http(monkeypatch, lambda request: _tokenfree_audio(json.loads(request.content)["model"]))
    persist = gateway._persist_tts_mp3
    calls = 0

    def fail_first(dest, audio):
        """只让第一份成功音频的本地处理失败。"""
        nonlocal calls
        calls += 1
        return False if calls == 1 else persist(dest, audio)
    monkeypatch.setattr(gateway, "_persist_tts_mp3", fail_first)
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == "tokenfree" and result.model == "gemini-3.1-flash-tts"
    assert result.is_fallback and result.usage_recorded
    assert [(row.model, row.cost_fen) for row in rows] == [("qwen3-omni-flash", 8), ("gemini-3.1-flash-tts", 8)]


async def test_volc_paid_response_is_recorded_before_local_write(db_session, monkeypatch, tmp_path):
    """付费音频已返回但本地写盘失败，转 edge 后仍保留火山成本。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _edge(monkeypatch, gateway)
    _http(monkeypatch, lambda request: _volc_audio() if request.url.host == "volc.test" else httpx.Response(503))
    write_bytes = Path.write_bytes

    def fail_volc_write(path, data):
        """只模拟火山已返回内容落盘时的本地磁盘错误。"""
        if data[3:4] == b"a":
            raise OSError("local write failed")
        return write_bytes(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_volc_write)
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == "edge"
    assert [(row.provider, row.cost_fen) for row in rows] == [("volc", 1)]


async def test_tokenfree_http_success_without_audio_does_not_bill(db_session, monkeypatch, tmp_path):
    """HTTP 200 仅有文本或错误信息，不当作付费音频成功响应。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    _edge(monkeypatch, gateway)
    _http(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "无音频"}}]}))
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    assert result.provider == "edge" and not result.usage_recorded
    assert await _events(db_session, task) == []


async def test_openai_voice_records_existing_per_character_cost_once(db_session, monkeypatch, tmp_path):
    """OpenAI 音色沿用原每字符费率，只由 gateway 记一条真实用量。"""
    from app.services import avatar_ai
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    settings = gateway.settings.model_copy(update={"avatar_ai_base_url": "https://avatar.test/v1", "avatar_ai_api_key": "test-avatar"})
    monkeypatch.setattr(avatar_ai, "get_settings", lambda: settings)
    _http(monkeypatch, lambda request: httpx.Response(200, content=b"ID3" + b"o" * 4000))
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好" * 100, "openai_shimmer", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == "avatar_ai" and result.usage_recorded
    assert len(rows) == 1
    assert rows[0].billing_key == "avatar_tts" and rows[0].provider == "avatar_ai"
    assert rows[0].cost_fen == kepu_avatar_billing.speech_cost_fen(200)


def test_usage_fields_exclude_unbounded_audio_and_response_content():
    """用量只保留有界数字，音频、prompt、任意响应及嵌套 payload 不可入库。"""
    from app.services.tts_billing import tts_usage_fields
    fields = tts_usage_fields({
        "usage": {"total_tokens": "100", "quota_consumed": 5000, "audio": "a" * 10000,
                  "prompt": "private", "cost": float("inf"), "extra": {"nested": "private"}},
        "data": "a" * 10000, "model": "m" * 10000, "quota": 10**50,
    })
    assert fields == {"usage": {"total_tokens": 100, "quota_consumed": 5000}}


async def test_mock_audio_never_creates_paid_usage(db_session, monkeypatch, tmp_path):
    """Mock 在测试目录生成音频并返回兼容 URL，不按付费估价。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    gateway.settings.ark_mock = True
    monkeypatch.setattr(ark, "__file__", str(tmp_path / "app/services/ark.py"))
    _edge(monkeypatch, gateway)
    async with billing_scope(task.id):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    assert result.provider == "mock" and result.usage_recorded is False
    assert result.url.startswith("/static/mock/")
    assert await _events(db_session, task) == []


async def test_tokenfree_binary_audio_uses_default_estimate_once(db_session, monkeypatch, tmp_path):
    """普通 speech 音频响应没有 usage 时，用原默认估量记一次实际模型。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(volc_tts, "is_configured", lambda: False)
    monkeypatch.setattr(gateway, "_resolved_audio_model", lambda: "elevenlabs-tts")
    # 生产 storage.project_dir 会创建目录，测试也保留该约定。
    (tmp_path / str(project.id)).mkdir()
    _http(monkeypatch, lambda request: httpx.Response(200, content=b"ID3" + b"s" * 4000))
    async with billing_scope(task.id), upstream_event_session(db_session):
        result = await gateway.tts_with_result("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert result.provider == "tokenfree" and result.model == "elevenlabs-tts"
    assert result.usage_recorded is True and result.is_fallback is False
    assert len(rows) == 1 and rows[0].model == result.model
    assert rows[0].total_tokens == get_settings().billing_est_tts_tokens
