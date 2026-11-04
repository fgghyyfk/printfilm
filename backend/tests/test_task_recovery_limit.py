"""任务中断回收封顶、按实退款与两个入口的数据库回归。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.models import UsageEvent, WalletLedger
from app.models_tasks import TaskRun, TaskStep
from app.models_drama import DramaAsset, DramaEpisode
from app.services.billing.settlement import settle_task
from app.services.tasks import executor, scheduler
from app.services.tasks.service import get_task_for_runtime
from tests.conftest import make_task, make_user


@pytest.mark.parametrize("brief", [True, False])
def test_legacy_null_count_is_readable_in_task_outputs(brief):
    """旧 NULL 在回收前也能返回任务输出，不能导致任务中心校验失败。"""
    from app.schemas_tasks import TaskRunBriefOut, TaskRunOut
    schema = TaskRunBriefOut if brief else TaskRunOut
    result = schema.model_validate({"id": 1, "domain": "kepu", "task_type": "compose", "status": "running",
                                    "priority": 100, "requested_by": 1, "recovery_count": None})
    assert result.recovery_count == 0


@pytest.fixture
def recovery_session(db_session, monkeypatch):
    """让真实恢复入口使用隔离测试事务，屏蔽费用提醒外调。"""
    @asynccontextmanager
    async def session():
        """复用当前测试的保存点事务。"""
        yield db_session
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", session)
    monkeypatch.setattr("app.services.billing.alerts.process_billing_alerts_after_charge", AsyncMock())
    return db_session


async def interrupted(db, task_id):
    """模拟一次实际运行后进程消失，直接 SQL 保留过期更新时间。"""
    await db.execute(update(TaskRun).where(TaskRun.id == task_id).values(
        status="running", updated_at=datetime.now(UTC) - timedelta(hours=1)))
    await db.commit()


@pytest.mark.parametrize("entry", ["scheduler", "executor", "execute"])
async def test_third_interruption_fails_and_only_refunds_unused_freeze(recovery_session, monkeypatch, entry):
    """前两次回 pending，第三次失败；100 冻结已用30只退70且重复处理幂等。"""
    db = recovery_session
    user = await make_user(db, balance_fen=900, frozen_fen=100)
    task = await make_task(db, user, domain="kepu", task_type="project_compose_only",
                           status="running", billing_status="frozen")
    task.billing_estimate_fen = 100
    db.add(TaskStep(task_id=task.id, step_key="compose", status="submitting"))
    usage = UsageEvent(user_id=user.id, task_run_id=task.id, billing_key="llm_chat",
                       model="mock", charge_fen=30)
    db.add(usage)
    await db.commit()
    if entry == "execute":
        @asynccontextmanager
        async def session():
            """实际执行器各阶段复用隔离事务。"""
            yield db
        monkeypatch.setattr(executor, "AsyncSessionLocal", session)
        handler = SimpleNamespace(executor=AsyncMock(side_effect=asyncio.CancelledError))
        monkeypatch.setattr(executor, "get_task_handler", lambda *_: handler)
    for count in range(1, 4):
        await interrupted(db, task.id)
        if entry == "scheduler":
            assert await scheduler.recover_orphaned_tasks() == 1
        elif entry == "executor":
            await executor._requeue_interrupted_task(db, await get_task_for_runtime(db, task.id))
        else:
            with pytest.raises(asyncio.CancelledError):
                await executor.execute_task_run(task.id)
        await db.refresh(task)
        assert task.status == ("pending" if count < 3 else "failed")
        assert getattr(task, "recovery_count", 0) == count
    assert "中断" in task.error_message and "3" in task.error_message
    assert (task.billing_charged_fen, task.billing_refunded_fen, task.billing_status) == (30, 70, "settled")
    assert user.balance_fen == 970 and user.frozen_fen == 0
    assert usage.settled is True
    assert await scheduler.recover_orphaned_tasks() == 0
    await executor._requeue_interrupted_task(db, await get_task_for_runtime(db, task.id))
    await settle_task(db, task.id)
    await db.commit()
    refunds = list(await db.scalars(select(WalletLedger).where(
        WalletLedger.ref_id == str(task.id), WalletLedger.kind == "unfreeze")))
    assert len(refunds) == 1 and refunds[0].delta_fen == 70
    assert task.recovery_count == 3 and user.balance_fen == 970


async def test_null_counter_and_configured_limit(recovery_session, monkeypatch):
    """升级前 NULL 计数从零开始，配置阈值也适用于执行器入口。"""
    from app.config import get_settings
    from sqlalchemy import text
    db = recovery_session
    user = await make_user(db)
    task = await make_task(db, user, status="running")
    # additive DDL 的旧表列允许 NULL，直接 SQL 模拟历史数据。
    await db.execute(text("UPDATE task_runs SET recovery_count=NULL WHERE id=:id"), {"id": task.id})
    await db.commit()
    monkeypatch.setattr(get_settings(), "task_runtime_max_recoveries", 1)
    await executor._requeue_interrupted_task(db, await get_task_for_runtime(db, task.id))
    await db.refresh(task)
    assert task.status == "failed" and task.recovery_count == 1


@pytest.mark.parametrize("alive", ["job", "job_after_scan", "voice", "voice_cancel"])
async def test_locally_alive_work_is_never_counted(recovery_session, monkeypatch, alive):
    """执行协程或显式音色 HTTP 仍活跃时，过期时间戳不能触发回收。"""
    db = recovery_session
    user = await make_user(db)
    task = await make_task(db, user, status="running")
    if alive in {"voice", "voice_cancel"}:
        task.result_payload = {"voice_billing_reservation": True}
        task.cancel_requested = alive == "voice_cancel"
        monkeypatch.setattr("app.services.billing.ephemeral.durable_voice_request_alive", lambda _: True)
    elif alive == "job_after_scan":
        checks = iter([False, True])
        monkeypatch.setattr(scheduler, "_job_alive", lambda _: next(checks))
    else:
        monkeypatch.setattr(scheduler, "_job_alive", lambda _: True)
    await db.commit()
    await interrupted(db, task.id)
    assert await scheduler.recover_orphaned_tasks() == 0
    await db.refresh(task)
    assert task.status == "running" and task.recovery_count == 0


@pytest.mark.parametrize("entry", ["scheduler", "executor"])
@pytest.mark.parametrize("domain,task_type,expected", [("drama", "fragment_video", "awaiting_poll"),
                                                       ("drama", "asset_video", "pending"),
                                                       ("drama", "voice_synthesis", "failed"),
                                                       ("api", "v1_video", "awaiting_review"),
                                                       ("studio", "video", "failed")])
async def test_provider_id_recovery_uses_only_supported_consumer(recovery_session, entry, domain, task_type, expected):
    """保留原上游号；已有消费者安全恢复，未知费用待核对而非盲目退款。"""
    db = recovery_session
    user = await make_user(db)
    task = await make_task(db, user, domain=domain, task_type=task_type, status="running")
    task.payload = {"provider_task_id": "existing-upstream"}
    await db.commit()
    await interrupted(db, task.id)
    if entry == "scheduler":
        await scheduler.recover_orphaned_tasks()
    else:
        await executor._requeue_interrupted_task(db, await get_task_for_runtime(db, task.id))
    await db.refresh(task)
    assert task.status == expected
    assert task.provider_task_id == "existing-upstream"
    assert getattr(task, "recovery_count", 0) == 1
    if expected == "failed":
        assert "上游" in task.error_message
    elif expected == "awaiting_review":
        assert "待核对" in task.error_message
        assert task.next_action_at is None
    else:
        assert task.next_action_at is not None


@pytest.mark.parametrize("entry", ["scheduler", "executor"])
@pytest.mark.parametrize("guard", ["terminal", "review", "user_cancel", "finalizing", "voice_orphan", "voice_orphan_cancel"])
async def test_recovery_does_not_count_protected_states(recovery_session, entry, guard):
    """终态、待核对、用户取消和收尾不消耗回收次数；音色孤儿保留冻结。"""
    db = recovery_session
    user = await make_user(db, balance_fen=900, frozen_fen=100)
    task = await make_task(db, user, domain="drama", task_type="voice_synthesis", status="running",
                           billing_status="frozen")
    task.billing_estimate_fen = 100
    if guard == "user_cancel":
        task.cancel_requested = True
    elif guard == "finalizing":
        task.cancel_requested = True
        task.payload = {"finalizing_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}
    elif guard in {"voice_orphan", "voice_orphan_cancel"}:
        task.result_payload = {"voice_billing_reservation": True}
        task.cancel_requested = guard == "voice_orphan_cancel"
    await db.commit()
    await interrupted(db, task.id)
    if guard in {"terminal", "review"}:
        task.status = "succeeded" if guard == "terminal" else "awaiting_review"
        await db.commit()
    if entry == "scheduler":
        await scheduler.recover_orphaned_tasks()
    else:
        await executor._requeue_interrupted_task(db, await get_task_for_runtime(db, task.id))
    await db.refresh(task)
    assert getattr(task, "recovery_count", 0) == 0
    expected = {"terminal": "succeeded", "review": "awaiting_review", "user_cancel": "cancelled",
                "finalizing": "running", "voice_orphan": "awaiting_review", "voice_orphan_cancel": "awaiting_review"}[guard]
    assert task.status == expected
    if guard in {"finalizing", "voice_orphan", "voice_orphan_cancel"}:
        assert task.billing_status == "frozen" and user.frozen_fen == 100


@pytest.mark.parametrize("new_owner", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_fragment_plan_limit_updates_only_current_owner(recovery_session, new_owner, legacy):
    """同 revision 的旧 force 任务到限不能覆盖当前任务；自己的 generating 必须失败可见。"""
    from tests.test_drama_fragment_save import make_episode
    db = recovery_session
    user, project, ep, _ = await make_episode(db)
    task = await make_task(db, user, domain="drama", task_type="fragment_plan", status="running")
    task.drama_project_id, task.episode_id = project.id, ep.id
    task.payload = {"expected_revision": ep.revision}
    task.recovery_count = 2
    owner_id = task.id
    if new_owner:
        newer = await make_task(db, user, domain="drama", task_type="fragment_plan")
        newer.episode_id, newer.drama_project_id = ep.id, project.id
        newer.payload = {"expected_revision": ep.revision}
        owner_id = newer.id
    ep.params = {"fragment_plan_status": "generating", "fragment_plan_revision": ep.revision,
                 "user_setting": "keep", **({} if legacy else {"fragment_plan_task_id": owner_id})}
    await db.commit()
    await interrupted(db, task.id)
    assert await scheduler.recover_orphaned_tasks() == 1
    await db.refresh(ep)
    assert ep.params["fragment_plan_status"] == ("generating" if new_owner else "failed")
    assert ep.params["user_setting"] == "keep"
    if not new_owner:
        assert "中断" in ep.params["fragment_plan_error"]


@pytest.mark.parametrize("entry", ["api", "auto"])
async def test_plan_enqueue_records_distinct_owner_in_same_commit(recovery_session, monkeypatch, entry):
    """相同 revision 两次 force 入队仍有不同 owner，每次提交都包含任务和 owner。"""
    from app.api.drama.episodes import plan_episode_fragments
    from app.schemas_drama import DramaPlanFragmentsRequest
    from tests.test_drama_fragment_save import make_episode
    db = recovery_session
    user, _, ep, _ = await make_episode(db)
    await db.commit()
    monkeypatch.setattr("app.services.billing.settlement.ensure_balance_for_task", AsyncMock(return_value=0))
    commit, owners = db.commit, []

    async def inspect_commit():
        """在真实提交边界核对 owner 已与当前任务同事务写入。"""
        await db.flush()
        params = await db.scalar(select(DramaEpisode.params).where(DramaEpisode.id == ep.id))
        owners.append(params.get("fragment_plan_task_id"))
        await commit()
    monkeypatch.setattr(db, "commit", inspect_commit)
    for _ in range(2):
        if entry == "api":
            await plan_episode_fragments(ep.id, DramaPlanFragmentsRequest(force=True), db, user)
        else:
            from app.services.drama.jobs import dispatch_episode_fragment_plan
            await dispatch_episode_fragment_plan(db, user, ep)
    assert len(owners) == 2 and all(owners) and owners[0] != owners[1]
    task = await db.get(TaskRun, owners[-1])
    assert task.episode_id == ep.id and task.payload["expected_revision"] == ep.revision


@pytest.mark.parametrize("target", ["kepu", "asset", "fragment"])
async def test_exhausted_recovery_keeps_existing_failure_projection(recovery_session, target):
    """到限复用标准失败路径，科普和漫剧资产/分镜的错误都可见。"""
    from tests.test_drama_fragment_save import make_episode
    from tests.test_kepu_task_recovery import seed_project
    from app.models import ProjectStatus
    db = recovery_session
    if target == "kepu":
        user, project, _ = await seed_project(db, status=ProjectStatus.COMPOSING)
        task = await make_task(db, user, domain="kepu", task_type="project_compose_only")
        task.project_id = project.id
    else:
        user, project, ep, fragments = await make_episode(db)
        task = await make_task(db, user, domain="drama", task_type="asset_image" if target == "asset" else "fragment_video")
        task.drama_project_id, task.episode_id = project.id, ep.id
        if target == "asset":
            asset = DramaAsset(project_id=project.id, params={"generation": {"status": "generating"}})
            db.add(asset)
            await db.flush()
            task.asset_id = asset.id
        else:
            task.fragment_id = fragments[0].id
    task.recovery_count = 2
    await db.commit()
    await interrupted(db, task.id)
    await scheduler.recover_orphaned_tasks()
    if target == "kepu":
        await db.refresh(project)
        assert project.status == ProjectStatus.FAILED and "中断" in project.error_msg
    else:
        row = asset if target == "asset" else fragments[0]
        await db.refresh(row)
        assert row.params["generation"]["status"] == "failed"


async def test_recovery_limit_cancels_later_sequential_tasks(recovery_session):
    """耗尽回收次数仍走标准失败，后续串行镜头不会继续占队列。"""
    from uuid import uuid4
    db = recovery_session
    user = await make_user(db)
    first = await make_task(db, user, domain="drama", task_type="fragment_video")
    second = await make_task(db, user, domain="drama", task_type="fragment_video")
    first.batch_key = second.batch_key = f"recovery-{uuid4().hex}"
    first.payload, second.payload = {"sequential": True, "batch_index": 0}, {"sequential": True, "batch_index": 1}
    first.recovery_count = 2
    await db.commit()
    await interrupted(db, first.id)
    await scheduler.recover_orphaned_tasks()
    await db.refresh(second)
    assert second.status == "cancelled" and second.error_code == "sequential_blocked"
