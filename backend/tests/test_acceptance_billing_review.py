"""人工核对必须保留既有费用、只补缺失费用且重复请求不重复扣退。"""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin import tasks as api
from app.models import UsageEvent, WalletLedger
from tests.conftest import make_task, make_user


@pytest.mark.parametrize('missing', [0, 25])
async def test_review_keeps_known_usage_and_replays_once(db_session, monkeypatch, missing):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    user = await make_user(db_session, balance_fen=900, frozen_fen=100)
    admin = await make_user(db_session)
    admin.is_admin = True
    task = await make_task(db_session, user, status='awaiting_review', billing_status='frozen', provider_task_id='original-provider')
    task.billing_estimate_fen = 100
    task.result_payload = {'video_billing_pending': {'reason': 'timeout'}, 'preview_url': 'existing-media'}
    usage = UsageEvent(user_id=user.id, task_run_id=task.id, billing_key='llm_chat', charge_fen=30, cost_fen=30)
    db_session.add(usage)
    await db_session.commit()
    monkeypatch.setattr('app.services.billing.alerts.process_billing_alerts_after_charge', AsyncMock())
    body = AdminTaskBillingReviewRequest(missing_cost_fen=missing, final_cost_confirmed=True, note='已按原任务核对收费凭证')
    await api.admin_review_task_billing(task.id, body, admin, db_session)
    await api.admin_review_task_billing(task.id, body, admin, db_session)
    await db_session.refresh(task)
    await db_session.refresh(user)
    assert task.billing_status == 'settled'
    assert task.billing_charged_fen == 30 + missing
    assert (user.balance_fen, user.frozen_fen) == (970 - missing, 0)
    assert task.provider_task_id == 'original-provider'
    assert task.result_payload['preview_url'] == 'existing-media'
    assert '已按原任务核对' not in json.dumps(task.result_payload, ensure_ascii=False)
    audit = list(await db_session.scalars(select(WalletLedger).where(WalletLedger.kind == 'billing_review', WalletLedger.ref_id == str(task.id))))
    assert len(audit) == 1
    assert str(admin.id) in audit[0].note
    with pytest.raises(HTTPException) as conflict:
        await api.admin_review_task_billing(task.id, AdminTaskBillingReviewRequest(missing_cost_fen=missing+1, final_cost_confirmed=True, note=body.note), admin, db_session)
    assert conflict.value.status_code == 409


async def test_review_refuses_live_request_and_historical_settlement(db_session, monkeypatch):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    user = await make_user(db_session)
    task = await make_task(db_session, user, status='awaiting_review', billing_status='frozen')
    body = AdminTaskBillingReviewRequest(missing_cost_fen=0, final_cost_confirmed=True, note='核对依据')
    monkeypatch.setattr('app.services.billing.ephemeral.durable_voice_request_alive', lambda _: True)
    with pytest.raises(HTTPException) as live:
        await api.admin_review_task_billing(task.id, body, user, db_session)
    assert live.value.status_code == 409
    monkeypatch.setattr('app.services.billing.ephemeral.durable_voice_request_alive', lambda _: False)
    task.billing_status = 'settled'
    await db_session.commit()
    with pytest.raises(HTTPException) as historical:
        await api.admin_review_task_billing(task.id, body, user, db_session)
    assert historical.value.status_code == 409


async def test_explicit_zero_failure_settles_but_missing_cost_stays_pending(db_session):
    from app.services.billing.ephemeral import settle_deferred_video_poll
    for raw, expected in [({'quota': 0}, 'settled'), ({}, 'frozen')]:
        user = await make_user(db_session, balance_fen=900, frozen_fen=100)
        task = await make_task(db_session, user, status='awaiting_poll', billing_status='frozen', provider_task_id=f'provider-{user.id}')
        task.billing_estimate_fen = 100
        await db_session.commit()
        await settle_deferred_video_poll(db_session, user, provider_task_id=task.provider_task_id, poll_status='failed', raw_usage=raw)
        await db_session.commit()
        await db_session.refresh(task)
        assert task.billing_status == expected


def test_real_provider_parser_keeps_explicit_zero_and_missing_separate():
    from app.services.ark import _build_task_result_from_payload
    zero = _build_task_result_from_payload({'status': 'failed', 'quota': 0})
    assert zero.upstream_cost_fen == 0
    assert zero.raw_usage['quota'] == 0
    assert _build_task_result_from_payload({'status': 'failed'}).upstream_cost_fen is None


async def test_known_positive_failed_cost_never_refetches_or_estimates(db_session, monkeypatch):
    from app.services.ark import TaskResult
    from app.services.drama.billing_util import prepare_seedance_video_usage
    fetch = AsyncMock(side_effect=AssertionError('明确费用无需重复查询'))
    monkeypatch.setattr('app.services.ark.get_ark', lambda: type('Ark', (), {'fetch_task_once': fetch})())
    usage = await prepare_seedance_video_usage(user_id=1, billing_key='seedance_audio', model='seedance-2.5', domain='api', provider_task_id='known', fallback_duration_sec=10, task_result=TaskResult(status='failed', upstream_cost_fen=17))
    assert (usage.cost_fen, usage.charge_fen, usage.estimated) == (17, 17, False)
    fetch.assert_not_awaited()


async def test_admin_review_http_rejects_regular_user():
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from app.deps import get_current_user
    app = FastAPI()
    app.include_router(api.router, prefix='/api/admin')
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role='user')
    app.dependency_overrides[api.get_db] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local.test') as client:
        response = await client.post('/api/admin/tasks/1/billing-review', json={'missing_cost_fen': 0, 'final_cost_confirmed': True, 'note': '已核对'})
    assert response.status_code == 403


@pytest.mark.parametrize("same_task", [True, False])
async def test_two_connections_review_records_one_missing_charge(committed_episode, monkeypatch, same_task):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    from app.models_tasks import TaskRun
    engine, user, _, _, _ = committed_episode
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user = await db.get(type(user), user.id)
        count = 1 if same_task else 2
        user.balance_fen, user.frozen_fen = 1000 - count * 100, count * 100
        task_ids = []
        for _ in range(count):
            task = await make_task(db, user, status='awaiting_review', billing_status='frozen')
            task.billing_estimate_fen = 100
            task.result_payload = {'tts_billing_pending': {'cost_unknown': True}}
            task_ids.append(task.id)
        await db.commit()
    monkeypatch.setattr('app.services.billing.alerts.process_billing_alerts_after_charge', AsyncMock())
    ready = asyncio.Event()
    body = AdminTaskBillingReviewRequest(missing_cost_fen=23, final_cost_confirmed=True, note='核对原调用未记账23分')
    async def attempt(task_id):
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await ready.wait()
            try:
                return await api.admin_review_task_billing(task_id, body, user, db)
            except HTTPException as exc:
                assert exc.status_code == 409
                return exc
    jobs = [asyncio.create_task(attempt(task_ids[0])), asyncio.create_task(attempt(task_ids[-1]))]
    ready.set()
    results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=10)
    # NOWAIT 碰撞短暂返回409；已提交后相同授权请求重放仍只产生一份费用。
    for task_id, result in zip([task_ids[0], task_ids[-1]], results):
        if isinstance(result, HTTPException):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await api.admin_review_task_billing(task_id, body, user, db)
    async with AsyncSession(engine) as db:
        owner = await db.get(type(user), user.id)
        assert (owner.balance_fen, owner.frozen_fen) == (1000 - count * 23, 0)
        usages = list(await db.scalars(select(UsageEvent).where(UsageEvent.task_run_id.in_(task_ids))))
        audits = list(await db.scalars(select(WalletLedger).where(WalletLedger.ref_id.in_([str(value) for value in task_ids]), WalletLedger.kind == 'billing_review')))
        assert len(usages) == len(audits) == count
        assert all(usage.cost_fen == usage.charge_fen == 23 for usage in usages)


from tests.test_drama_fragment_save import committed_episode  # noqa: F401


async def test_late_unknown_poll_cannot_reopen_review_after_manual_settlement(committed_episode, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    from app.services.billing import settlement
    from app.services.billing.ephemeral import settle_deferred_video_poll
    from app.models_tasks import TaskRun
    engine, user, _, _, _ = committed_episode
    async with AsyncSession(engine, expire_on_commit=False) as db:
        owner = await db.get(type(user), user.id)
        owner.balance_fen, owner.frozen_fen = 900, 100
        task = await make_task(db, owner, status='awaiting_poll', billing_status='frozen', provider_task_id='late-unknown')
        task.billing_estimate_fen = 100
        await db.commit()
        task_id = task.id
    waiting, resume = asyncio.Event(), asyncio.Event()
    original_lock = settlement._lock_task
    async def lock(db, task_id):
        if asyncio.current_task().get_name() == 'late-poll':
            waiting.set()
            await resume.wait()
        return await original_lock(db, task_id)
    monkeypatch.setattr(settlement, '_lock_task', lock)
    async def late_poll():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await settle_deferred_video_poll(db, user, provider_task_id='late-unknown', poll_status='failed')
            await db.commit()
    job = asyncio.create_task(late_poll(), name='late-poll')
    await waiting.wait()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        task = await db.get(TaskRun, task_id)
        task.status = 'awaiting_review'
        task.result_payload = {'video_billing_pending': {'reason': 'earlier-timeout'}}
        await db.commit()
        await api.admin_review_task_billing(task_id, AdminTaskBillingReviewRequest(missing_cost_fen=0, final_cost_confirmed=True, note='原任务明确无新增费用'), user, db)
    resume.set()
    await asyncio.wait_for(job, 5)
    async with AsyncSession(engine) as db:
        task = await db.get(TaskRun, task_id)
        assert task.billing_status == 'settled' and task.status == 'failed'
        assert not task.result_payload.get('video_billing_pending')


async def test_serialized_audit_limit_rejects_without_any_write(db_session):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    user = await make_user(db_session, balance_fen=900, frozen_fen=100)
    task = await make_task(db_session, user, status='awaiting_review', billing_status='frozen')
    await db_session.commit()
    with pytest.raises(HTTPException) as rejected:
        await api.admin_review_task_billing(task.id, AdminTaskBillingReviewRequest(missing_cost_fen=11, final_cost_confirmed=True, note='"' * 120), user, db_session)
    assert rejected.value.status_code == 422
    assert user.balance_fen == 900 and user.frozen_fen == 100
    assert not list(await db_session.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id)))


async def test_review_finishes_bound_tool_record_without_erasing_media(db_session):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    from app.models import ToolRun
    user = await make_user(db_session, balance_fen=900, frozen_fen=100)
    task = await make_task(db_session, user, domain='studio', task_type='tool_video', status='awaiting_review', billing_status='frozen', provider_task_id='original-tool')
    task.billing_estimate_fen = 100
    task.result_payload = {'video_billing_pending': {'reason': 'timeout'}}
    tool = ToolRun(user_id=user.id, tool_id='video', kind='video', status='awaiting_review', task_id='original-tool', params={'billing_task_id': task.id}, urls=['existing.mp4'], preview_url='preview.jpg')
    db_session.add(tool)
    await db_session.commit()
    await api.admin_review_task_billing(task.id, AdminTaskBillingReviewRequest(missing_cost_fen=0, final_cost_confirmed=True, note='明确无未记账费用'), user, db_session)
    await db_session.refresh(tool)
    assert tool.status == 'failed'
    assert tool.urls == ['existing.mp4'] and tool.preview_url == 'preview.jpg'
    assert '已核对' in tool.error


@pytest.mark.parametrize('new_owner', [False, True])
async def test_manual_review_closes_fragment_gate_without_overwriting_new_generation(db_session, new_owner):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    from tests.test_drama_fragment_save import make_episode
    user, project, episode, rows = await make_episode(db_session)
    fragment = rows[0]
    fragment.video = 'keep-old.mp4'
    fragment.params = {'generation': {'status': 'running'}, 'video_versions': [{'url': 'keep-history.mp4'}], 'custom': 'keep'}
    task = await make_task(db_session, user, domain='drama', task_type='fragment_video', status='awaiting_review', billing_status='skipped', provider_task_id='old-provider')
    task.drama_project_id, task.episode_id, task.fragment_id = project.id, episode.id, fragment.id
    task.result_payload = {'video_billing_pending': {'reason': 'interrupted'}}
    if new_owner:
        newer = await make_task(db_session, user, domain='drama', task_type='fragment_video', status='succeeded')
        newer.fragment_id, newer.episode_id, newer.drama_project_id = fragment.id, episode.id, project.id
    await db_session.commit()
    await api.admin_review_task_billing(task.id, AdminTaskBillingReviewRequest(missing_cost_fen=0, final_cost_confirmed=True, note='原费用已全部核对'), user, db_session)
    await db_session.refresh(fragment)
    assert fragment.params['generation']['status'] == ('running' if new_owner else 'failed')
    assert fragment.video == 'keep-old.mp4' and fragment.params['video_versions'] == [{'url': 'keep-history.mp4'}]
    assert fragment.params['custom'] == 'keep'


async def test_manual_review_recovers_kepu_local_status_and_step(db_session):
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    from app.models import ProjectStatus
    from app.models_tasks import TaskStep
    from tests.test_kepu_task_recovery import seed_project
    user, project, _ = await seed_project(db_session, status=ProjectStatus.VIDEOING)
    task = await make_task(db_session, user, domain='kepu', task_type='project_pipeline', status='awaiting_review', billing_status='skipped')
    task.project_id = project.id
    step = TaskStep(task_id=task.id, step_key='video', status='submitting')
    db_session.add(step)
    await db_session.commit()
    await api.admin_review_task_billing(task.id, AdminTaskBillingReviewRequest(missing_cost_fen=0, final_cost_confirmed=True, note='当前所有调用费用均已核对'), user, db_session)
    await db_session.refresh(project)
    await db_session.refresh(step)
    assert project.status == ProjectStatus.FAILED
    assert project.final_video_url == '/static/old.mp4'
    assert step.status == 'failed' and step.finished_at is not None


@pytest.mark.parametrize('held', ['task', 'wallet'])
async def test_review_lock_conflict_rolls_back_target_lock_without_deadlock(committed_episode, held):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models_tasks import TaskRun
    from app.models import User
    from app.schemas_tasks import AdminTaskBillingReviewRequest
    engine, user, project, episode, rows = committed_episode
    async with AsyncSession(engine, expire_on_commit=False) as db:
        task = await make_task(db, user, domain='drama', task_type='fragment_video', status='awaiting_review')
        task.drama_project_id, task.episode_id, task.fragment_id = project.id, episode.id, rows[0].id
        await db.commit()
        task_id = task.id
    async with AsyncSession(engine) as holder:
        statement = select(TaskRun).where(TaskRun.id == task_id) if held == 'task' else select(User).where(User.id == user.id)
        await holder.execute(statement.with_for_update())
        async with AsyncSession(engine) as db:
            with pytest.raises(HTTPException) as conflict:
                await asyncio.wait_for(api.admin_review_task_billing(task_id, AdminTaskBillingReviewRequest(missing_cost_fen=10, final_cost_confirmed=True, note='明确原费用已核对'), user, db), timeout=2)
            assert conflict.value.status_code == 409
        # 失败已回滚目标锁；其他事务立即可锁定分集。
        async with AsyncSession(engine) as probe:
            await probe.execute(select(type(episode)).where(type(episode).id == episode.id).with_for_update(nowait=True))
    async with AsyncSession(engine) as db:
        assert not list(await db.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task_id)))


@pytest.mark.parametrize('cost', [0, 17])
async def test_refetched_explicit_cost_uses_same_exact_billing_path(monkeypatch, cost):
    from app.services.ark import TaskResult
    from app.services.drama.billing_util import prepare_seedance_video_usage
    fetch = AsyncMock(return_value=TaskResult(status='failed', raw_usage={'quota': 0} if cost == 0 else None, upstream_cost_fen=cost, total_tokens=123, completion_tokens=120))
    monkeypatch.setattr('app.services.ark.get_ark', lambda: type('Ark', (), {'fetch_task_once': fetch})())
    usage = await prepare_seedance_video_usage(user_id=1, billing_key='seedance_audio', model='seedance-2.5', domain='api', provider_task_id='refresh-cost', fallback_duration_sec=10, task_result=TaskResult(status='failed'))
    assert (usage.cost_fen, usage.charge_fen, usage.estimated) == (cost, cost, False)
    assert (usage.total_tokens, usage.completion_tokens) == (123, 120)
    assert fetch.await_count == 1
