"""分镜删除/重拆：锁外对账与提交后的管理员通知。"""
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BillingAlertNotification, UsageEvent, User, WalletLedger
from app.models_drama import DramaEpisode
from app.models_tasks import TaskRun
from app.services.billing.newapi_bill import (
    apply_newapi_bills as real_apply_newapi_bills,
    prepare_newapi_bill_logs as real_prepare_newapi_bill_logs,
)
from app.services.tokenfree_usage import quota_to_cost_fen
from tests.test_drama_fragment_save import committed_episode, request  # noqa: F401 — pytest fixture


@pytest_asyncio.fixture
async def frozen_fragment_work(committed_episode, monkeypatch):
    """真实 frozen 钱包与未对账用量，全部记录只在独立测试库内提交并清理。"""
    engine, user, project, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tasks = []
    logs = []
    async with factory() as db:
        current = await db.get(User, user.id)
        current.balance_fen, current.frozen_fen = 98_000, 2_000
        for row in rows:
            task = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id,
                           status='running', episode_id=ep.id, fragment_id=row.id,
                           billing_status='frozen', billing_estimate_fen=1_000)
            db.add(task)
            await db.flush()
            provider = f'image-{task.id}'
            db.add(UsageEvent(user_id=user.id, drama_project_id=project.id, task_run_id=task.id,
                              domain='drama', billing_key='seedream', model='seedream-5.0',
                              total_tokens=100_000, cost_fen=1, charge_fen=1, estimated=True,
                              raw_usage_json=json.dumps({'provider_task_id': provider}),
                              settled=False, created_at=datetime.now(UTC)))
            tasks.append(task)
            logs.append({'id': task.id, 'request_id': provider, 'model_name': 'seedream-5.0', 'quota': 500_000})
        await db.commit()
    monkeypatch.setattr('app.services.billing.newapi_bill.apply_newapi_bills', real_apply_newapi_bills)
    monkeypatch.setattr('app.services.billing.newapi_bill.prepare_newapi_bill_logs', real_prepare_newapi_bill_logs)
    monkeypatch.setattr('app.services.billing.newapi_bill.fetch_tokenfree_consume_logs', AsyncMock(return_value=logs))
    try:
        yield engine, factory, user, project, ep, rows, tasks, logs
    finally:
        async with factory() as db:
            await db.execute(delete(WalletLedger).where(WalletLedger.user_id == user.id))
            await db.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
            await db.execute(delete(BillingAlertNotification).where(BillingAlertNotification.user_id == user.id))
            await db.execute(delete(TaskRun).where(TaskRun.requested_by == user.id))
            await db.commit()


@pytest.mark.parametrize('writer', ['save', 'seed'])
async def test_frozen_cancel_bill_network_occurs_before_episode_lock(frozen_fragment_work, monkeypatch, writer):
    """真实取消/结算必须读取quota，同时日志网络期间第二连接可以取得分集锁。"""
    from app.api.drama.episodes import save_fragments
    from app.services.drama.seed import _replace_episode_fragments
    engine, factory, user, _, ep, rows, tasks, logs = frozen_fragment_work
    observed = []
    async def fetch(*args):
        """替代消费日志网络，并用真实 NOWAIT 验证锁边界。"""
        async with AsyncSession(engine) as probe:
            try:
                await probe.scalar(select(DramaEpisode.id).where(DramaEpisode.id == ep.id).with_for_update(nowait=True))
                observed.append(True)
            except DBAPIError:
                observed.append(False)
        return logs
    monkeypatch.setattr('app.services.billing.newapi_bill.fetch_tokenfree_consume_logs', fetch)
    async with factory() as db:
        if writer == 'save':
            await save_fragments(ep.id, request([], deleted_ids=[rows[0].id]), db, user)
        else:
            current = await db.get(DramaEpisode, ep.id)
            await _replace_episode_fragments(db, current, 'body', [], drafts=[])
            await db.commit()
        assert observed and all(observed)
        cancelled = await db.get(TaskRun, tasks[0].id)
        assert cancelled.status == 'cancelled'
        assert cancelled.billing_status == 'settled'
        assert cancelled.billing_charged_fen == quota_to_cost_fen(500_000)
        await db.commit()
        # 结算重入保持幂等，不再退还或扣减。
        from app.services.billing.settlement import settle_task
        before = (await db.get(User, user.id)).balance_fen
        await settle_task(db, tasks[0].id)
        await db.commit()
        assert (await db.get(User, user.id)).balance_fen == before


async def test_llm_usage_preparation_does_not_hold_user_fk_lock_before_episode_claim(frozen_fragment_work, monkeypatch):
    """规划费用先建快照，不在占有分集前持 User FK 锁与删除结算反向等待。"""
    from app.models_drama import DramaScript
    from app.services.drama import seed
    from app.services.drama.jobs import run_episode_fragment_plan_job
    engine, factory, user, project, ep, _, _, _ = frozen_fragment_work
    async with factory() as db:
        db.add(DramaScript(project_id=project.id, name='script', episode_content=[{'episodeNumber': 1, 'body': '正文'}]))
        await db.commit()
    monkeypatch.setattr('app.services.drama.jobs.AsyncSessionLocal', factory)
    monkeypatch.setattr('app.services.drama.fragment_plan.plan_fragments_with_llm', AsyncMock(return_value=[{'content': '新镜'}]))
    monkeypatch.setattr('app.services.drama.character_intro_llm.prepare_character_intro_overrides', AsyncMock(return_value={}))
    original = seed.replace_episode_fragments_with_drafts
    observed = []
    async def apply(db, *args, **kwargs):
        """在真实CAS入口前，第二连接应能取得User排他行锁。"""
        async with AsyncSession(engine) as probe:
            try:
                await probe.scalar(select(User.id).where(User.id == user.id).with_for_update(nowait=True))
                observed.append(True)
            except DBAPIError:
                observed.append(False)
        return await original(db, *args, **kwargs)
    monkeypatch.setattr(seed, 'replace_episode_fragments_with_drafts', apply)
    result = await run_episode_fragment_plan_job(ep.id, expected_revision=0, force=True)
    assert result['ok'] is True
    assert observed == [True]
    async with factory() as db:
        events = list((await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id, UsageEvent.billing_key == 'llm_chat'))).all())
        assert len(events) == 1
        assert events[0].cost_fen > 0


@pytest.mark.parametrize('writer', ['save', 'llm'])
@pytest.mark.parametrize('late', ['event', 'task'])
async def test_cancellation_snapshot_rejects_uncovered_usage_and_rolls_back_editor_state(frozen_fragment_work, monkeypatch, writer, late):
    """日志等待期间出现未覆盖用量，整次编辑409回滚，LLM真实成本仍保留且不误发通知。"""
    from app.api.drama.episodes import save_fragments
    from app.models_drama import DramaScript, DramaEpisodeFragment
    from app.services.drama.jobs import run_episode_fragment_plan_job
    from fastapi import HTTPException
    _, factory, user, project, ep, rows, tasks, logs = frozen_fragment_work
    calls = 0
    async def fetch(*args):
        """最后一次日志读取暂停点插入新用量，保证前面任务已准备完快照。"""
        nonlocal calls
        calls += 1
        if calls == 2:
            async with factory() as db:
                target = tasks[1]
                if late == 'task':
                    target = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id,
                                     status='running', episode_id=ep.id, fragment_id=rows[1].id,
                                     billing_status='frozen', billing_estimate_fen=1_000)
                    db.add(target)
                    await db.flush()
                    current = await db.get(User, user.id)
                    current.frozen_fen += 1_000
                    current.balance_fen -= 1_000
                db.add(UsageEvent(user_id=user.id, task_run_id=target.id, billing_key='seedream', model='seedream-5.0',
                                  cost_fen=2, charge_fen=2, settled=False, created_at=datetime.now(UTC)))
                await db.commit()
        return logs
    monkeypatch.setattr('app.services.billing.newapi_bill.fetch_tokenfree_consume_logs', fetch)
    if writer == 'save':
        async with factory() as db:
            with pytest.raises(HTTPException) as exc:
                await save_fragments(ep.id, request([], deleted_ids=[row.id for row in rows]), db, user)
            assert exc.value.status_code == 409
    else:
        async with factory() as db:
            db.add(DramaScript(project_id=project.id, name='script', episode_content=[{'episodeNumber': 1, 'body': '正文'}]))
            await db.commit()
        monkeypatch.setattr('app.services.drama.jobs.AsyncSessionLocal', factory)
        monkeypatch.setattr('app.services.drama.fragment_plan.plan_fragments_with_llm', AsyncMock(return_value=[{'content': '过期新镜'}]))
        monkeypatch.setattr('app.services.drama.character_intro_llm.prepare_character_intro_overrides', AsyncMock(return_value={}))
        result = await run_episode_fragment_plan_job(ep.id, expected_revision=0, force=True)
        assert result == {'ok': False, 'error': '内容已在别处更新，请刷新'}
    async with factory() as db:
        current = await db.get(DramaEpisode, ep.id)
        assert current.revision == 0
        assert {row.id for row in (await db.scalars(select(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == ep.id))).all()} == {row.id for row in rows}
        current_tasks = list((await db.scalars(select(TaskRun).where(TaskRun.requested_by == user.id))).all())
        assert all(task.status == 'running' and task.billing_status == 'frozen' for task in current_tasks)
        assert not list((await db.scalars(select(WalletLedger).where(WalletLedger.user_id == user.id))).all())
        image_events = list((await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id, UsageEvent.billing_key == 'seedream'))).all())
        assert all(not event.settled for event in image_events)
        if writer == 'llm':
            actual_llm = list((await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id, UsageEvent.billing_key == 'llm_chat'))).all())
            assert len(actual_llm) == 1 and actual_llm[0].cost_fen > 0


async def test_usage_arriving_after_settlement_read_is_outside_fragment_edit_cutoff(frozen_fragment_work, monkeypatch):
    """结算实际读点之后的迟到用量保留未结，明确不在本项追加扣费或扩大写锁协议。"""
    from app.api.drama.episodes import save_fragments
    _, factory, user, _, ep, rows, tasks, _ = frozen_fragment_work
    late_id = None
    async def apply(events, **kwargs):
        """apply入口已在真实结算读点之后，此时另一连接仍可写入迟到event。"""
        nonlocal late_id
        async with factory() as db:
            late = UsageEvent(user_id=user.id, task_run_id=tasks[0].id, billing_key='seedream', model='seedream-5.0',
                              cost_fen=2, charge_fen=2, settled=False, created_at=datetime.now(UTC))
            db.add(late)
            await db.commit()
            late_id = late.id
        return await real_apply_newapi_bills(events, **kwargs)
    monkeypatch.setattr('app.services.billing.newapi_bill.apply_newapi_bills', apply)
    async with factory() as db:
        saved = await save_fragments(ep.id, request([], deleted_ids=[rows[0].id]), db, user)
        assert saved.revision == 1
    async with factory() as db:
        late = await db.get(UsageEvent, late_id)
        task = await db.get(TaskRun, tasks[0].id)
        assert late.settled is False
        assert task.billing_charged_fen == quota_to_cost_fen(500_000)
