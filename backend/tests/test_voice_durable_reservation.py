"""显式音色外调前的真实提交边界与孤儿恢复。"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User, UsageEvent, WalletLedger
from app.models_drama import DramaAsset, DramaProject
from app.models_tasks import TaskRun
from app.services import billing, tts_billing
from app.services.tasks import scheduler
from tests.test_tts_billing import _gateway, _http, _volc_audio, _tokenfree_audio, _edge
from tests.test_tts_billing_drama import _drama_voice


@pytest.mark.parametrize("mode", ["success", "poisoned", "prior_paid", "blocked", "scan_race"])
async def test_voice_reservation_is_durable_before_paid_http(db_session, monkeypatch, tmp_path, mode):
    """独立 PG 连接必须看见冻结；计费连接故障和 stale 恢复不能退尽费用。"""
    from app.api.drama.generation import generate_voice
    from app.schemas_drama import DramaVoiceGenerateRequest
    from app.services.drama import voice_synthesis
    from app.services.billing import settle_task

    sessions = async_sessionmaker(db_session.bind.engine, expire_on_commit=False)
    gateway = _gateway(monkeypatch, tmp_path)
    _edge(monkeypatch, gateway, fail=True)
    monkeypatch.setattr(voice_synthesis, "get_ark", lambda: gateway)
    monkeypatch.setattr(voice_synthesis, "finalize_voice_reference_url", lambda url, **kw: url)
    monkeypatch.setattr(tts_billing, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    if mode == "prior_paid":
        monkeypatch.setattr("app.services.ark.is_near_silent_audio",
                            lambda path: path.read_bytes()[3:4] == b"a")
    entered, release = asyncio.Event(), asyncio.Event()
    seen, cost_sessions = [], []
    real_record, real_rollback = billing.record_line, AsyncSession.rollback
    uid = pid = None
    job = None
    async with sessions() as business:
        user, project, asset = await _drama_voice(business)
        await business.commit()
        uid, pid, aid = user.id, project.id, asset.id

        async def response(request):
            """真实独立连接在付费请求发生前观察已提交冻结。"""
            business_pid = await business.scalar(text("SELECT pg_backend_pid()"))
            async with sessions() as observer:
                assert await observer.scalar(text("SELECT pg_backend_pid()")) != business_pid
                task = await observer.scalar(select(TaskRun).where(TaskRun.requested_by == uid))
                wallet = await observer.get(User, uid)
                seen.append((task.id if task else None, wallet.frozen_fen))
            entered.set()
            if mode in {"blocked", "scan_race"}:
                await release.wait()
            return _volc_audio() if request.url.host == "volc.test" else _tokenfree_audio()
        _http(monkeypatch, response)

        async def record(db, **kw):
            """计费必须独立于资产事务；受控 SQL 错误保持计费连接中毒。"""
            assert db is not business
            cost_sessions.append(db)
            if mode == "poisoned" or (mode == "prior_paid" and kw["provider"] == "tokenfree"):
                await db.execute(text("SELECT 1 / 0"))
            return await real_record(db, **kw)
        monkeypatch.setattr(billing, "record_line", record)

        async def rollback(db):
            """在实际清理前抛错，业务会话仍应保持可用。"""
            if db in cost_sessions and mode in {"poisoned", "prior_paid"}:
                raise RuntimeError("rollback unavailable before cleanup")
            await real_rollback(db)
        monkeypatch.setattr(AsyncSession, "rollback", rollback)
        try:
            job = asyncio.create_task(generate_voice(DramaVoiceGenerateRequest(
                project_id=pid, asset_id=aid, voice_prompt="青年男声", speaker="narrator_calm",
            ), db=business, user=user))
            await asyncio.wait_for(entered.wait(), 5)
            assert len(seen) == 1 and seen[0][0] and seen[0][1] > 0
            tid = seen[0][0]
            if mode == "scan_race":
                async with sessions() as observer:
                    await observer.execute(update(TaskRun).where(TaskRun.id == tid).values(
                        updated_at=datetime.now(UTC) - timedelta(hours=1)))
                    await observer.commit()
                scan_read, resume_scan = asyncio.Event(), asyncio.Event()

                from contextlib import asynccontextmanager
                @asynccontextmanager
                async def scan_session():
                    """真实恢复连接读出旧 running 快照后暂停，等待 HTTP 完成。"""
                    async with sessions() as db:
                        execute = db.execute
                        first_query = True
                        async def paused_execute(*args, **kwargs):
                            """只暂停 bulk 扫描，不暂停后续加锁重读。"""
                            nonlocal first_query
                            result = await execute(*args, **kwargs)
                            if first_query:
                                first_query = False
                                scan_read.set()
                                await resume_scan.wait()
                            return result
                        monkeypatch.setattr(db, "execute", paused_execute)
                        yield db
                monkeypatch.setattr(scheduler, "AsyncSessionLocal", scan_session)
                scan = asyncio.create_task(scheduler.recover_orphaned_tasks())
                try:
                    await asyncio.wait_for(scan_read.wait(), 5)
                    release.set()
                    await job
                finally:
                    resume_scan.set()
                    await scan
                async with sessions() as observer:
                    finished = await observer.get(TaskRun, tid)
                    assert finished.status == "succeeded" and finished.billing_status == "settled"
                    assert "tts_billing_pending" not in finished.result_payload
                    paid = list(await observer.scalars(select(UsageEvent).where(UsageEvent.task_run_id == tid)))
                    assert len(paid) == 1 and paid[0].settled is True
                return
            if mode == "blocked":
                async with sessions() as observer:
                    await observer.execute(update(TaskRun).where(TaskRun.id == tid).values(
                        updated_at=datetime.now(UTC) - timedelta(hours=1)))
                    await observer.commit()
                await scheduler.recover_orphaned_tasks()
                async with sessions() as observer:
                    assert (await observer.get(TaskRun, tid)).status == "running"
                # HTTP 真正中断后只保留已提交预扣；恢复不可重排 voice noop 或零成本全退。
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                await scheduler.recover_orphaned_tasks()
                async with sessions() as observer:
                    task = await observer.get(TaskRun, tid)
                    wallet = await observer.get(User, uid)
                    assert task.status == "awaiting_review" and task.next_action_at is None
                    assert task.result_payload["tts_billing_pending"]["cost_unknown"] is True
                    assert wallet.frozen_fen == task.billing_estimate_fen > 0
                    await settle_task(observer, tid)
                    assert task.billing_status == "frozen"
                return
            release.set()
            if mode in {"poisoned", "prior_paid"}:
                from fastapi import HTTPException
                with pytest.raises(HTTPException, match="费用待核对") as pending:
                    await job
                assert pending.value.headers["X-Error-Code"] == "billing_review_pending"
                assert "请勿重复生成" in pending.value.detail
            else:
                await job
            async with sessions() as observer:
                task = await observer.get(TaskRun, tid)
                wallet = await observer.get(User, uid)
                if mode in {"poisoned", "prior_paid"}:
                    assert task.status == "awaiting_review" and task.billing_status == "frozen"
                    assert task.billing_refunded_fen == 0
                    assert task.result_payload["tts_billing_pending"]["cost_fen"] == (8 if mode == "prior_paid" else 1)
                    assert wallet.frozen_fen == task.billing_estimate_fen > 0
                    if mode == "prior_paid":
                        paid = list(await observer.scalars(select(UsageEvent).where(UsageEvent.task_run_id == tid)))
                        assert len(paid) == 1 and paid[0].provider == "volc" and paid[0].cost_fen == 1
                else:
                    assert task.status == "succeeded" and wallet.frozen_fen == 0
            assert len(seen) == len(cost_sessions) == (2 if mode == "prior_paid" else 1)
        finally:
            release.set()
            if job is not None:
                await asyncio.gather(job, return_exceptions=True)
            await real_rollback(business)
            # 此用例真实提交到独立 PG 连接，显式删除本用户的测试行。
            async with sessions() as cleanup:
                await cleanup.execute(delete(UsageEvent).where(UsageEvent.user_id == uid))
                await cleanup.execute(delete(WalletLedger).where(WalletLedger.user_id == uid))
                await cleanup.execute(delete(TaskRun).where(TaskRun.requested_by == uid))
                await cleanup.execute(delete(DramaAsset).where(DramaAsset.project_id == pid))
                await cleanup.execute(delete(DramaProject).where(DramaProject.id == pid))
                await cleanup.execute(delete(User).where(User.id == uid))
                await cleanup.commit()


async def test_reservation_commit_failure_never_calls_executor(db_session, monkeypatch):
    """持久预扣提交失败必须在任何上游调用前停止。"""
    from unittest.mock import AsyncMock
    from app.services.billing import run_billed_ephemeral
    from tests.conftest import make_user
    user = await make_user(db_session)
    executor = AsyncMock()
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=RuntimeError("reservation commit failed")))
    with pytest.raises(RuntimeError, match="reservation commit failed"):
        await run_billed_ephemeral(db_session, user, domain="drama", task_type="voice_synthesis",
                                   executor=executor, commit_before_execute=True)
    executor.assert_not_awaited()
