"""工具视频后台收尾与历史记录投影。"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, func

from app.models import ToolRun, UsageEvent, WalletLedger
from app.services.tasks import poller
from app.services.studio_tools import update_tool_run_task, list_tool_runs
from tests.conftest import make_task, make_user


async def tool_record(db, user, task):
    """创建绑定唯一计费任务的旧式进行中记录。"""
    row = ToolRun(user_id=user.id, tool_id='t2v', kind='video', status='queued',
                  task_id=task.provider_task_id, params={'billing_task_id': task.id}, error='旧错误')
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_background_skipped_success_updates_record_without_wallet(db_session):
    user = await make_user(db_session, balance_fen=2000)
    task = await make_task(db_session, user, domain='studio', task_type='tool_video',
                           status='awaiting_poll', billing_status='skipped', provider_task_id='tool-video-off')
    row = await tool_record(db_session, user, task)
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    with patch.object(poller, 'AsyncSessionLocal', session), patch(
        'app.services.studio_tools.poll_video_task', AsyncMock(return_value={
            'status': 'succeeded', 'kind': 'video', 'urls': ['/static/result.mp4'],
            'usage': {'total_tokens': 100, 'completion_tokens': 100},
        }),
    ):
        await poller._poll_ephemeral_deferred_tasks()
    await db_session.refresh(row)
    assert row.status == 'succeeded'
    assert row.urls == ['/static/result.mp4']
    assert row.error is None
    assert task.status == 'succeeded' and task.billing_status == 'settled'
    assert user.balance_fen == 2000 and user.frozen_fen == 0
    assert await db_session.scalar(select(func.count()).select_from(WalletLedger).where(WalletLedger.user_id == user.id)) == 0
    assert await db_session.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.task_run_id == task.id)) == 1


@pytest.mark.asyncio
async def test_record_terminal_is_monotonic_and_wrong_task_is_ignored(db_session):
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain='studio', task_type='tool_video', provider_task_id='tool-video-guard')
    row = await tool_record(db_session, user, task)
    await update_tool_run_task(db_session, user.id, task.provider_task_id, {'status': 'succeeded', 'urls': ['/static/a.mp4']}, billing_task_id=task.id + 1)
    assert row.status == 'queued'
    await update_tool_run_task(db_session, user.id, task.provider_task_id, {'status': 'succeeded', 'urls': ['/static/a.mp4']}, billing_task_id=task.id)
    await update_tool_run_task(db_session, user.id, task.provider_task_id, {'status': 'running'}, billing_task_id=task.id)
    assert row.status == 'succeeded' and row.error is None


@pytest.mark.asyncio
async def test_historical_success_projects_snapshot_without_financial_change(db_session):
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain='studio', task_type='tool_video', status='succeeded', billing_status='settled', provider_task_id='tool-history')
    task.result_payload = {'status': 'succeeded', 'urls': ['/static/history.mp4']}
    row = await tool_record(db_session, user, task)
    rows, _ = await list_tool_runs(db_session, user.id, page=1, page_size=8)
    assert next(r for r in rows if r.id == row.id).urls == ['/static/history.mp4']
    assert row.status == 'succeeded' and row.error is None
    assert task.billing_status == 'settled'


@pytest.mark.asyncio
@pytest.mark.parametrize('expired', [False, True])
async def test_background_failure_and_timeout_update_record(db_session, expired):
    from datetime import timedelta
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain='studio', task_type='tool_video',
                           status='awaiting_poll', billing_status='skipped', provider_task_id=f'tool-failure-{expired}')
    task.started_at = datetime.now(UTC) - timedelta(hours=1) if expired else datetime.now(UTC)
    row = await tool_record(db_session, user, task)
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    upstream = AsyncMock(return_value={'status': 'failed', 'error': 'upstream error with secret'})
    with patch.object(poller, 'AsyncSessionLocal', session), patch('app.services.studio_tools.poll_video_task', upstream):
        await poller._poll_one_ephemeral_task(task.id, now=datetime.now(UTC), timeout_sec=900)
    assert row.status == task.status == 'awaiting_review'
    assert row.error and 'secret' not in row.error
    assert upstream.await_count == (0 if expired else 1)


@pytest.mark.asyncio
async def test_historical_missing_url_only_queries_trusted_original_route(db_session):
    from types import SimpleNamespace
    from app.models_tasks import TaskEvent
    from app.api.tools import _record_out
    from app.services.tool_video_records import restore_terminal_tool_records
    user = await make_user(db_session)
    tasks, rows = [], []
    for index in range(2):
        task = await make_task(db_session, user, domain='studio', task_type='tool_video',
                               status='succeeded', billing_status='settled', provider_task_id=f'history-{index}')
        tasks.append(task)
        rows.append(await tool_record(db_session, user, task))
    db_session.add(TaskEvent(task_id=tasks[0].id, event_type='upstream.request.accepted', payload={
        'provider': 'tokenfree', 'provider_task_id': tasks[0].provider_task_id,
        'is_video_submit': True, 'endpoint': 'https://synthetic.invalid/tasks',
    }))
    await db_session.flush()
    upstream = AsyncMock(return_value={'status': 'failed', 'urls': [], 'error': 'not found'})
    with patch('app.services.ark.get_ark', return_value=SimpleNamespace(_url=lambda path: 'https://synthetic.invalid/tasks')), patch('app.services.studio_tools.poll_video_task', upstream):
        await restore_terminal_tool_records(db_session, user.id, rows)
    assert upstream.await_count == 1 and upstream.await_args.args[1] == tasks[0].provider_task_id
    for row, task in zip(rows, tasks):
        assert row.status == task.status == 'succeeded'
        assert task.billing_status == 'settled'
        assert '待恢复' in row.error
        output = _record_out(row).model_dump()
        assert '待恢复' in output['error'] and '生成失败' not in output['error']


@pytest.mark.asyncio
async def test_concurrent_finalizers_prepare_without_locks_and_charge_once(db_session, monkeypatch):
    """真实两连接同时准备费用，NOWAIT 证明外调不占行锁，最终只结算一次。"""
    import asyncio
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import User
    from app.models_tasks import TaskRun
    from app.services.billing.ephemeral import settle_deferred_video_poll
    sessions = async_sessionmaker(db_session.bind.engine, expire_on_commit=False)
    async with sessions() as setup:
        user = await make_user(setup, balance_fen=1900, frozen_fen=100)
        task = await make_task(setup, user, domain='studio', task_type='tool_video',
                               status='awaiting_poll', billing_status='frozen', provider_task_id='concurrent-tool')
        task.billing_estimate_fen = 100
        row = await tool_record(setup, user, task)
        await setup.commit()
        uid, tid, rid = user.id, task.id, row.id
    both = asyncio.Event()
    prepared_count = 0

    async def prepare_logs(events):
        nonlocal prepared_count
        async with sessions() as probe:
            assert await probe.scalar(select(TaskRun.id).where(TaskRun.id == tid).with_for_update(nowait=True)) == tid
            assert await probe.scalar(select(ToolRun.id).where(ToolRun.id == rid).with_for_update(nowait=True)) == rid
        prepared_count += 1
        if prepared_count == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        return []

    monkeypatch.setattr('app.services.billing.newapi_bill.prepare_newapi_bill_logs', prepare_logs)

    async def finalize():
        async with sessions() as db:
            user = await db.get(User, uid)
            await settle_deferred_video_poll(db, user, provider_task_id='concurrent-tool',
                poll_status='succeeded', billing_task_id=tid, usage_tokens=100,
                raw_usage={'usage': {'cost_fen': 30}}, result_data={'urls': ['/static/concurrent.mp4']})
            await db.commit()
    try:
        await asyncio.wait_for(asyncio.gather(finalize(), finalize()), 10)
        async with sessions() as db:
            task, row, user = await db.get(TaskRun, tid), await db.get(ToolRun, rid), await db.get(User, uid)
            assert row.status == task.status == 'succeeded'
            assert row.urls == ['/static/concurrent.mp4']
            assert user.frozen_fen == 0
            assert user.balance_fen == 2000 - task.billing_charged_fen
            assert await db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.task_run_id == tid)) == 1
            assert await db.scalar(select(func.count()).select_from(WalletLedger).where(WalletLedger.user_id == uid, WalletLedger.kind == 'settle')) == 1
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(ToolRun).where(ToolRun.id == rid))
            await cleanup.execute(delete(UsageEvent).where(UsageEvent.user_id == uid))
            await cleanup.execute(delete(WalletLedger).where(WalletLedger.user_id == uid))
            await cleanup.execute(delete(TaskRun).where(TaskRun.id == tid))
            await cleanup.execute(delete(User).where(User.id == uid))
            await cleanup.commit()


@pytest.mark.asyncio
async def test_history_with_wrong_owner_or_billing_task_does_not_query(db_session):
    """不可信历史关联不代理 GET，也不投影其他用户或其他类型任务。"""
    from app.services.tool_video_records import restore_terminal_tool_records
    user, other = await make_user(db_session), await make_user(db_session)
    task = await make_task(db_session, other, domain='studio', task_type='tool_video',
                           status='succeeded', billing_status='settled', provider_task_id='wrong-owner')
    row = await tool_record(db_session, user, task)
    upstream = AsyncMock()
    with patch('app.services.studio_tools.poll_video_task', upstream):
        await restore_terminal_tool_records(db_session, user.id, [row])
    assert row.status == 'queued' and upstream.await_count == 0
