"""付费音频已成功但本地记账失败时，禁止重复调用或退尽已知成本。"""
from contextlib import asynccontextmanager

import pytest
from tests.tts_database import db_session
from sqlalchemy import select, text

from app.models_tasks import TaskRun
from app.services import ark, billing
from app.services.billing import run_billed_ephemeral, settle_task
from app.services.billing.context import billing_scope
from app.services.tasks import executor
from app.services.tasks.handlers import TaskHandler
from tests.test_tts_billing import _kepu_task, _gateway, _http, _volc_audio, _events


@pytest.mark.parametrize("entry", ["ephemeral", "executor"])
async def test_pending_tts_usage_preserves_freeze_across_settle_and_cancel(
    db_session, monkeypatch, tmp_path, entry,
):
    """真实 SQL 记账持续失败后，TaskRun 待核对并保冻结，不能被结算/取消退尽。"""
    user, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    requests = []
    attempts = []

    def response(request):
        """唯一的真实付费音频成功响应。"""
        requests.append(request.url.host)
        assert request.url.host == "volc.test"
        return _volc_audio()
    _http(monkeypatch, response)

    async def broken_record(db, **kwargs):
        """记录实际本地 SQL 错误，不模拟服务商失败。"""
        attempts.append(kwargs)
        await db.execute(text("SELECT 1 / 0"))
    monkeypatch.setattr(billing, "record_line", broken_record)

    async def synthesize(*args):
        """外层任务内使用真实 gateway 与真实数据库事务。"""
        return await gateway.tts("你好", "narrator_calm", project_id=project.id)

    if entry == "ephemeral":
        with pytest.raises(RuntimeError, match="计费"):
            await run_billed_ephemeral(
                db_session, user, domain="drama", task_type="voice_synthesis", executor=synthesize, commit_before_execute=True,
            )
        task = (await db_session.scalars(select(TaskRun).where(
            TaskRun.requested_by == user.id, TaskRun.task_type == "voice_synthesis"))).one()
    else:
        @asynccontextmanager
        async def session():
            """执行器复用真实已提交业务会话，费用另用独立连接。"""
            yield db_session
        monkeypatch.setattr(executor, "AsyncSessionLocal", session)
        monkeypatch.setattr(executor, "get_task_handler", lambda *args: TaskHandler("kepu", "project_pipeline", synthesize))
        await executor.execute_task_run(task.id)
    await db_session.refresh(task)
    await db_session.refresh(user)
    assert requests == ["volc.test"]
    assert len(attempts) == 2  # 只重试本地落账，绝不重发合成
    assert task.status == "awaiting_review"
    assert task.billing_status == "frozen"
    assert task.finished_at is None and task.next_action_at is None
    assert task.current_step_status == "awaiting_review" and task.progress_percent < 100
    frozen = task.billing_estimate_fen
    assert frozen > 0 and user.frozen_fen == frozen
    assert user.balance_fen == 100_000 - frozen
    pending = task.result_payload["tts_billing_pending"]
    assert pending["provider"] == "volc" and pending["cost_fen"] == 1
    assert pending["model"].startswith("volc/")
    assert pending["audio_url"]
    assert len(str(pending)) < 4000 and "data" not in pending
    assert await db_session.scalar(text("SELECT 1")) == 1
    assert await _events(db_session, task) == []
    await settle_task(db_session, task.id)
    await db_session.refresh(user)
    assert task.status == "awaiting_review" and task.billing_status == "frozen"
    assert user.balance_fen == 100_000 - frozen and user.frozen_fen == frozen
    # 分镜保存/删除/seed 的真实作废入口会覆盖 error_code，也必须保留已知成本的冻结。
    from app.services.tasks.service import _mark_task_cancelled_stale
    from datetime import UTC, datetime
    task.domain = "drama"
    task.task_type = "fragment_video"
    task.payload = {**task.payload, "fragment_ids": [991]}
    task.status = "awaiting_review"
    await db_session.flush()
    await _mark_task_cancelled_stale(db_session, task, "分镜已变更", datetime.now(UTC))
    await db_session.refresh(user)
    assert task.status == "awaiting_review" and task.billing_status == "frozen"
    assert user.balance_fen == 100_000 - frozen and user.frozen_fen == frozen
    task = await executor.get_task_for_runtime(db_session, task.id)
    await executor._mark_cancelled(db_session, task)
    await db_session.refresh(user)
    assert task.status == "awaiting_review" and task.billing_status == "frozen"
    assert user.balance_fen == 100_000 - frozen and user.frozen_fen == frozen


async def test_volc_usage_does_not_replace_existing_character_rate(db_session, monkeypatch, tmp_path):
    """火山成功响应带 usage 时，仍保原按字符费率，并保留真实用量。"""
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)

    def response(request):
        """在完成消息中附加字符服务常见的 usage 信息。"""
        body = _volc_audio().text.replace('{"code": 20000000}', '{"code": 20000000, "usage": {"total_tokens": 5}}')
        import httpx
        return httpx.Response(200, text=body)
    _http(monkeypatch, response)
    async with billing_scope(task.id):
        await gateway.tts("字" * 1000, "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert len(rows) == 1 and rows[0].cost_fen == 50
    assert rows[0].total_tokens == 5


async def test_client_payload_cannot_forge_billing_pending_guard(db_session):
    """客户端可传任务 payload，但不能只伪造同名字段就阻止正常退款。"""
    from app.services.billing import freeze_for_task
    user, project, task = await _kepu_task(db_session)
    task.payload = {"tts_billing_pending": {"provider": "volc", "cost_fen": 1}}
    await freeze_for_task(db_session, task)
    await settle_task(db_session, task.id)
    await db_session.refresh(user)
    assert task.billing_status == "settled"
    assert task.status != "awaiting_review"
    assert user.balance_fen == 100_000 and user.frozen_fen == 0


async def test_commit_confirmation_failure_is_not_retried(db_session, monkeypatch, tmp_path):
    """提交已完成但确认丢失时，不重复插用量行；保留待核对错误。"""
    from app.services import tts_billing
    _, project, task = await _kepu_task(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    factory = tts_billing.AsyncSessionLocal

    @asynccontextmanager
    async def session():
        """真实独立提交成功后，仅确认丢失，不能再插第二行。"""
        async with factory() as db:
            commit = db.commit
            async def uncertain_commit():
                """数据库已提交，客户端确认异常。"""
                await commit()
                raise RuntimeError("commit acknowledgement lost")
            monkeypatch.setattr(db, "commit", uncertain_commit)
            yield db
    monkeypatch.setattr(tts_billing, "AsyncSessionLocal", session)
    async with billing_scope(task.id):
        with pytest.raises(RuntimeError, match="计费"):
            await gateway.tts("你好", "narrator_calm", project_id=project.id)
    rows = await _events(db_session, task)
    assert len(rows) == 1


async def test_http_mock_result_cannot_forge_top_level_pending(db_session, monkeypatch):
    """HTTP 返回内容只能成为 echo，无法伪造服务端待核对标记或阻止结算。"""
    import httpx
    from app.main import app
    from app.database import get_db
    from app.deps import get_current_user
    from tests.conftest import make_user

    user = await make_user(db_session)
    user.role = "admin"
    await db_session.commit()

    async def db_override():
        """HTTP API 仍只使用专用测试数据库。"""
        yield db_session

    async def user_override():
        """使用受控管理员访问现有测试入口。"""
        return user

    @asynccontextmanager
    async def session():
        """执行器与 ASGI 共享测试回滚域。"""
        yield db_session
    monkeypatch.setattr(executor, "AsyncSessionLocal", session)
    forged = {"tts_billing_pending": {"provider": "volc", "cost_fen": 1}}
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/tasks/mock-delay", json={
                "delay_seconds": 1, "result_payload": forged,
            })
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
    assert response.status_code == 200, response.text
    task_id = response.json()["id"]
    await executor.execute_task_run(task_id)
    task = await db_session.get(TaskRun, task_id)
    await db_session.refresh(user)
    assert task.result_payload["echo"] == forged
    assert "tts_billing_pending" not in task.result_payload
    assert task.status == "succeeded" and task.billing_status == "settled"
    assert user.balance_fen == 100_000 and user.frozen_fen == 0
    await settle_task(db_session, task_id)
    assert task.status == "succeeded" and task.billing_status == "settled"


async def test_parallel_kepu_keeps_billing_pending_even_when_image_also_fails(db_session, monkeypatch, tmp_path):
    """真实并行图/音编排不能用普通图错误覆盖已付费音频的待核对快照。"""
    from unittest.mock import AsyncMock
    from app.models import Shot
    from app.services import pipeline, tts_billing
    user, project, task = await _kepu_task(db_session)
    project.pipeline_mode = "image_text"
    db_session.add(Shot(project_id=project.id, shot_no=1, narration="你好", duration=4.0))
    await db_session.commit()
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(gateway, "gen_image", AsyncMock(side_effect=RuntimeError("image down")))
    monkeypatch.setattr(pipeline, "get_ark", lambda: gateway)
    monkeypatch.setattr(pipeline, "_set_status", AsyncMock())
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(pipeline, "_continuous_audio_ok", lambda pid: False)
    requests = []

    def response(request):
        """唯一一次实际配音成功。"""
        requests.append(request.url.host)
        return _volc_audio()
    _http(monkeypatch, response)

    async def broken_record(db, **kwargs):
        """真实 SQL 错误只发生在用量写入中。"""
        await db.execute(text("SELECT 1 / 0"))
    monkeypatch.setattr(billing, "record_line", broken_record)

    @asynccontextmanager
    async def session():
        """业务编排使用测试业务会话；费用使用独立连接。"""
        yield db_session

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    monkeypatch.setattr(executor, "AsyncSessionLocal", session)

    async def run(task):
        """走真实并行图音函数，而非直接调用配音 helper。"""
        await pipeline._parallel_image_and_audio(project.id)
    monkeypatch.setattr(executor, "get_task_handler", lambda *args: TaskHandler("kepu", "project_pipeline", run))
    await executor.execute_task_run(task.id)
    await db_session.refresh(task)
    await db_session.refresh(user)
    assert requests == ["volc.test"]
    assert task.status == "awaiting_review" and task.billing_status == "frozen"
    assert user.frozen_fen == task.billing_estimate_fen > 0
    assert task.result_payload["tts_billing_pending"]["cost_fen"] == 1


@pytest.mark.parametrize("boundary", ["session_rollback", "session_close"])
async def test_unconfirmed_rollback_or_close_never_retries_usage(db_session, monkeypatch, tmp_path, boundary):
    """独立费用会话清理前失败或关闭失败均不能再次落账。"""
    from app.services import tts_billing
    from app.services.billing import freeze_for_task
    user, project, task = await _kepu_task(db_session)
    await freeze_for_task(db_session, task)
    await db_session.commit()
    gateway = _gateway(monkeypatch, tmp_path)
    requests, attempts = [], []
    factory = tts_billing.AsyncSessionLocal

    def response(request):
        """只允许一次实际付费响应。"""
        requests.append(request.url.host)
        return _volc_audio()
    _http(monkeypatch, response)

    async def broken_record(db, **kwargs):
        """真实 SQL 错误只污染独立费用连接。"""
        assert db is not db_session
        attempts.append(kwargs)
        await db.execute(text("SELECT 1 / 0"))
    monkeypatch.setattr(billing, "record_line", broken_record)

    @asynccontextmanager
    async def session():
        """实际回滚之前抛错；真实 Session 上下文仍负责释放测试连接。"""
        async with factory() as db:
            async def uncertain_rollback():
                """不执行实际清理就报告失败。"""
                raise RuntimeError("rollback unavailable before cleanup")
            if boundary == "session_rollback":
                monkeypatch.setattr(db, "rollback", uncertain_rollback)
            yield db
            if boundary == "session_close":
                raise RuntimeError("session close acknowledgement lost")
    monkeypatch.setattr(tts_billing, "AsyncSessionLocal", session)
    async with billing_scope(task.id):
        with pytest.raises(tts_billing.TTSBillingPending) as info:
            await gateway.tts("你好", "narrator_calm", project_id=project.id)
    assert len(attempts) == 1 and requests == ["volc.test"]
    assert await db_session.scalar(text("SELECT 1")) == 1
    tts_billing.mark_tts_billing_pending(task, info.value)
    await db_session.flush()
    await settle_task(db_session, task.id)
    assert task.status == "awaiting_review" and task.billing_status == "frozen"
    assert user.frozen_fen == task.billing_estimate_fen > 0
