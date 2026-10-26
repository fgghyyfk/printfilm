"""文字任务不排队：事务边界、媒体隔离、计费与可取消的真实执行生命周期。"""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base
from app.models import UsageEvent, User
from app.models_drama import DramaProject
from app.models_tasks import TaskRun
from app.schemas_tasks import TaskCreateRequest
from app.services.tasks import direct, executor, scheduler, service
from app.services.tasks.execution_policy import is_direct_llm_task, llm_task_condition
from app.services.tasks.handlers import TaskHandler
from app.services.tasks.queues import queue_stats


@pytest.fixture
async def direct_db(tmp_path, monkeypatch):
    """独立文件库让请求会话与真实后台会话共享已提交记录，不访问上游。"""
    import app.main  # noqa: F401
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "direct.db").as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(executor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "_running_jobs", {})
    monkeypatch.setattr(scheduler, "_running_queues", {})
    monkeypatch.setattr(get_settings(), "task_runtime_enabled", False)
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=300))
    yield sessions
    jobs = list(scheduler._running_jobs.values())
    for job in jobs:
        job.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)
    await engine.dispose()


@pytest.mark.parametrize("domain,kind,payload,expected", [
    ("drama", "script_summary", {}, True), ("drama", "episode_script", {}, True),
    ("drama", "fragment_plan", {}, True), ("drama", "seed_assets", {}, True),
    ("kepu", "project_pipeline", {"phase": "script"}, True),
    ("kepu", "project_pipeline", {}, False), ("kepu", "project_pipeline", {"phase": "assets"}, False),
    ("drama", "voice_synthesis", {}, False), ("studio", "tool_audio", {}, False),
    ("drama", "asset_image", {}, False), ("drama", "fragment_video", {}, False),
])
def test_only_pure_llm_jobs_bypass_queue(domain, kind, payload, expected):
    assert is_direct_llm_task(domain, kind, payload) is expected


@pytest.mark.asyncio
async def test_commit_false_rollback_and_savepoints_never_launch_uncommitted_tasks(direct_db, monkeypatch):
    """内层提交等外层提交，内层回滚只撤回自己的任务，根回滚不调用模型。"""
    launch = Mock()
    monkeypatch.setattr(direct, "launch_direct_task", launch)
    async with direct_db() as db:
        await db.execute(select(1))
        direct.defer_until_commit(db, 1, "one")
        async with db.begin_nested():
            direct.defer_until_commit(db, 2, "two")
        launch.assert_not_called()
        savepoint = await db.begin_nested()
        direct.defer_until_commit(db, 3, "rolled-back")
        await savepoint.rollback()
        await db.commit()
        assert launch.call_args_list == [((1, "one"),), ((2, "two"),)]
        launch.reset_mock()
        await db.execute(select(1))
        direct.defer_until_commit(db, 4, "four")
        await db.rollback()
        await db.commit()
        launch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["script_summary", "fragment_plan"])
async def test_immediate_llm_runs_with_runtime_disabled_and_media_full_and_settles(direct_db, monkeypatch, task_type):
    """无需调度器 tick；文字真正执行、扣费且不计入媒体队列占用。"""
    monkeypatch.setattr(direct, "launch_direct_task", scheduler.start_direct_task)
    started, finish = asyncio.Event(), asyncio.Event()

    async def run(task):
        """模拟模型返回并持久化用量，只在允许结束后返回。"""
        started.set()
        await finish.wait()
        async with direct_db() as db:
            db.add(UsageEvent(user_id=task.requested_by, task_run_id=task.id, billing_key="llm_chat", charge_fen=100, settled=False))
            await db.commit()
        return {"ok": True, "summary": "真实执行结果"}

    handler = TaskHandler("drama", task_type, run)
    monkeypatch.setattr(service, "get_task_handler", lambda *_: handler)
    monkeypatch.setattr(executor, "get_task_handler", lambda *_: handler)
    async with direct_db() as db:
        user = User(email="direct@example.test", hashed_password="test", balance_fen=5000)
        db.add(user)
        await db.flush()
        project = DramaProject(user_id=user.id, title="直接执行")
        db.add(project)
        # 同用户的文字/音频槽及图片/视频全部被占用，也不能阻塞新文字任务。
        for kind, queue in (("tool_audio", "text"), ("tool_image", "image"), ("tool_video", "video")):
            db.add(TaskRun(requested_by=user.id, domain="studio", task_type=kind, queue_type=queue, status="running", billing_status="skipped", payload={}))
        await db.commit()
        task = await service.create_task(db, user, TaskCreateRequest(domain="drama", task_type=task_type, drama_project_id=project.id))
        ident, user_id = task.id, user.id
        await asyncio.wait_for(started.wait(), 3)
        assert task.execution_mode == "direct"
        assert scheduler.running_count() == 0
        assert await service.count_user_active_runtime_tasks(db, user_id, queue_type="text") == 1
        assert (await queue_stats(db, get_settings()))[0]["active"] == 1
        job = scheduler._running_jobs[ident]
        finish.set()
        await asyncio.wait_for(job, 3)
    async with direct_db() as db:
        task = await db.get(TaskRun, ident)
        user = await db.get(User, user_id)
        assert (task.status, task.billing_status, task.billing_charged_fen) == ("succeeded", "settled", 100)
        assert (user.balance_fen, user.frozen_fen) == (4900, 0)
        assert task.result_payload["summary"] == "真实执行结果"


@pytest.mark.asyncio
async def test_sql_policy_retains_mixed_pipeline_and_excludes_inline_llm(direct_db):
    """JSON 缺少 phase 时不能被 SQL NULL 错误排除；聊天不能占配音槽。"""
    async with direct_db() as db:
        user = User(email="policy@example.test", hashed_password="test")
        db.add(user)
        await db.flush()
        for domain, kind, payload in (("kepu", "project_pipeline", {}), ("drama", "agent_chat", {}), ("kepu", "project_pipeline", {"phase": "script"}), ("studio", "tool_audio", {})):
            db.add(TaskRun(requested_by=user.id, domain=domain, task_type=kind, status="running", payload=payload))
        await db.commit()
        queued = list((await db.execute(select(TaskRun.task_type).where(~llm_task_condition(TaskRun)))).scalars())
        assert queued == ["project_pipeline", "tool_audio"]


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
async def test_direct_task_can_cancel_without_scheduler_and_refunds(direct_db, monkeypatch, admin):
    """运行时关闭时取消也立即生效，预扣退回，不遗留冻结。"""
    monkeypatch.setattr(direct, "launch_direct_task", scheduler.start_direct_task)
    entered = asyncio.Event()

    async def wait_model(task):
        """模拟尚无用量返回的长模型调用。"""
        entered.set()
        await asyncio.Event().wait()

    handler = TaskHandler("drama", "script_summary", wait_model)
    monkeypatch.setattr(service, "get_task_handler", lambda *_: handler)
    monkeypatch.setattr(executor, "get_task_handler", lambda *_: handler)
    async with direct_db() as db:
        user = User(email="cancel@example.test", hashed_password="test", balance_fen=1000)
        db.add(user)
        await db.flush()
        project = DramaProject(user_id=user.id, title="取消直接执行")
        db.add(project)
        await db.commit()
        task = await service.create_task(db, user, TaskCreateRequest(domain="drama", task_type="script_summary", drama_project_id=project.id))
        await asyncio.wait_for(entered.wait(), 3)
        job = scheduler._running_jobs[task.id]
        if admin:
            await service.cancel_task_admin(db, task.id)
        else:
            await service.cancel_task_for_user(db, user, task.id)
        await asyncio.gather(job, return_exceptions=True)
        ident, user_id = task.id, user.id
    async with direct_db() as db:
        task, user = await db.get(TaskRun, ident), await db.get(User, user_id)
        assert (task.status, task.billing_status, user.balance_fen, user.frozen_fen) == ("cancelled", "settled", 1000, 0)


@pytest.mark.asyncio
async def test_old_pending_text_starts_even_when_media_slots_full(direct_db, monkeypatch):
    """升级前 pending 文字任务无需等待旧的文字队列名额。"""
    async with direct_db() as db:
        user = User(email="pending@example.test", hashed_password="test")
        db.add(user)
        await db.flush()
        task = TaskRun(domain="drama", task_type="seed_assets", requested_by=user.id, status="pending", next_action_at=datetime.now(UTC), payload={})
        db.add(task)
        await db.commit()
        ident = task.id
    launch = Mock()
    monkeypatch.setattr(scheduler, "start_direct_task", launch)
    await scheduler._claim_direct_llm_tasks(datetime.now(UTC))
    launch.assert_called_once()
    assert launch.call_args.args[0] == ident
    await scheduler._claim_direct_llm_tasks(datetime.now(UTC))
    assert launch.call_count == 1
    async with direct_db() as db:
        row = await db.get(TaskRun, ident)
        assert row.status == "running" and row.lease_token


@pytest.mark.asyncio
async def test_explicit_kepu_script_phase_never_falls_through_to_media(monkeypatch):
    """已有脚本时，重试文字阶段不能隐式生成图片、配音或视频。"""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.services import pipeline

    @asynccontextmanager
    async def session():
        """只提供 phase 规范化所需项目查询。"""
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(pipeline_mode="full"))))

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock())
    monkeypatch.setattr(pipeline, "_resume_plan", AsyncMock(return_value=(False, True, False, False)))
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    media = AsyncMock()
    monkeypatch.setattr(pipeline, "_parallel_image_and_audio", media)
    monkeypatch.setattr(pipeline, "_parallel_videos", media)
    monkeypatch.setattr(pipeline, "_compose_stage", media)
    await pipeline.run_pipeline(123, phase="script")
    media.assert_not_awaited()
    assert pipeline.publish_progress.call_args.args[1]["stage"] == "SCRIPT_READY"


@pytest.mark.asyncio
async def test_remote_cancel_waits_for_direct_lease_owner_before_refund(direct_db, monkeypatch):
    """非执行节点不能提前退款；租约失效后才由恢复逻辑收尾。"""
    async with direct_db() as db:
        user = User(email="remote-cancel@example.test", hashed_password="test")
        db.add(user)
        await db.flush()
        task = TaskRun(domain="drama", task_type="script_summary", requested_by=user.id, status="cancel_requested", cancel_requested=True, payload={}, lease_until=datetime.now(UTC) + timedelta(minutes=10), lease_token="remote")
        db.add(task)
        await db.commit()
        ident = task.id
    cancel = AsyncMock()
    monkeypatch.setattr(scheduler, "_mark_cancelled", cancel)
    await scheduler._run_cancel_request(ident)
    cancel.assert_not_awaited()
    async with direct_db() as db:
        task = await db.get(TaskRun, ident)
        task.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    await scheduler._run_cancel_request(ident)
    cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_lease_heartbeat_notices_cancel_requested_by_another_node(monkeypatch):
    """执行节点通过租约心跳接收远端取消，而非把状态变更误认为任务完成。"""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)), scalar=AsyncMock(return_value=True), commit=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(scheduler, "AsyncSessionLocal", session)
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())
    cancel = AsyncMock()
    monkeypatch.setattr(scheduler, "cancel_running_task", cancel)
    await scheduler._renew_lease(123, "owner-token")
    cancel.assert_awaited_once_with(123)
