"""外部验收：取消生命周期与计划停服必须区别于异常中断。"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import pipeline
from app.services.tasks import executor, scheduler
from app.services.tasks.handlers import _await_kepu
from tests.conftest import make_task, make_user


async def test_cancelled_compose_exits_before_retry_and_siblings_keep_cancel(monkeypatch):
    # 正常完成后会冻结产出快照（读库）；本用例只验证取消信号归属
    monkeypatch.setattr('app.services.tasks.handlers._kepu_output_snapshot', AsyncMock(return_value={}))
    entered, leave = asyncio.Event(), asyncio.Event()
    # 合并后（线上规则）开始前已请求取消的任务直接跳过不执行；这里模拟“执行中才请求取消”
    task = SimpleNamespace(id=71, project_id=7001, cancel_requested=False)
    async def work():
        entered.set()
        await leave.wait()
        assert pipeline.is_cancelled(task.project_id)
        raise pipeline.PipelineCancelled('任务已取消')
    job = asyncio.create_task(_await_kepu(task, work()))
    await entered.wait()
    task.cancel_requested = True
    pipeline.cancel_pipeline(task.project_id)
    leave.set()
    assert (await job)['cancelled']
    async def retry():
        assert not pipeline.is_cancelled(task.project_id)
    await _await_kepu(SimpleNamespace(id=72, project_id=7001, cancel_requested=False), retry())


async def test_parallel_shots_do_not_clear_each_others_cancel():
    ready, exit_first, exit_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entries = 0
    async def work(gate):
        nonlocal entries
        entries += 1
        if entries == 2: ready.set()
        await gate.wait()
        assert pipeline.is_cancelled(7002)
        raise pipeline.PipelineCancelled('任务已取消')
    tasks = [SimpleNamespace(id=i, project_id=7002, cancel_requested=False) for i in (81, 82)]
    jobs = [asyncio.create_task(_await_kepu(t, work(gate)))
            for t, gate in [(tasks[0], exit_first), (tasks[1], exit_second)]]
    await ready.wait()
    for t in tasks:
        t.cancel_requested = True  # 执行中请求取消（开始前已取消的任务按线上规则直接跳过）
    pipeline.cancel_pipeline(7002)
    exit_first.set()
    await jobs[0]
    exit_second.set()
    await jobs[1]


async def test_planned_scheduler_stop_preserves_recovery_budget(db_session, monkeypatch):
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain='kepu', task_type='project_compose_only', status='leased')
    task.recovery_count = 2
    await db_session.commit()
    entered = asyncio.Event()
    async def body(_task):
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: raise asyncio.CancelledError()  # 内层可能重新抛出，不保留 args
    @asynccontextmanager
    async def session(): yield db_session
    monkeypatch.setattr(executor, 'AsyncSessionLocal', session)
    monkeypatch.setattr(executor, 'get_task_handler', lambda *_: SimpleNamespace(executor=body))
    monkeypatch.setattr(scheduler, '_scheduler_task', None)
    monkeypatch.setattr(scheduler, '_maintenance_task', None)
    monkeypatch.setattr(scheduler, '_running_jobs', {})
    monkeypatch.setattr(scheduler, '_cancel_jobs', {})
    job = asyncio.create_task(executor.execute_task_run(task.id))
    scheduler._running_jobs[task.id] = job
    await entered.wait()
    await scheduler.stop_scheduler()
    await db_session.refresh(task)
    assert task.status == 'pending'
    assert task.recovery_count == 2


async def test_cancelled_compose_worker_keeps_api_busy_until_real_thread_exit(db_session, monkeypatch):
    import threading
    from fastapi import HTTPException
    from app.api import projects
    from tests.test_kepu_task_recovery import seed_project
    user, project, _ = await seed_project(db_session)
    task = await make_task(db_session, user, domain='kepu', task_type='project_compose_only', status='leased')
    task.project_id = project.id
    await db_session.commit()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
    async def body(task):
        return await _await_kepu(task, pipeline._run_ffmpeg_compose_with_retry(project.id, worker))
    @asynccontextmanager
    async def session(): yield db_session
    monkeypatch.setattr(executor, 'AsyncSessionLocal', session)
    monkeypatch.setattr(pipeline, 'AsyncSessionLocal', session)
    monkeypatch.setattr(executor, 'get_task_handler', lambda *_: SimpleNamespace(executor=body))
    job = asyncio.create_task(executor.execute_task_run(task.id))
    try:
        await entered.wait()
        await projects.cancel_project(project.id, db_session, user)
        job.cancel()
        await asyncio.sleep(0)
        with pytest.raises(HTTPException) as busy:
            await projects.compose_only(project.id, db_session, user)
        assert busy.value.status_code == 409 and not job.done()
        # 第二次取消也不能把线程丢在后台。
        job.cancel(); await asyncio.sleep(0)
        assert not job.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await job
    await projects.compose_only(project.id, db_session, user)
    assert not pipeline.is_cancelled(project.id)


async def test_planned_stop_does_not_write_user_cancelled_project(db_session, monkeypatch):
    from tests.test_kepu_task_recovery import seed_project
    from app.models import ProjectStatus
    user, project, _ = await seed_project(db_session, status=ProjectStatus.SCRIPTING)
    @asynccontextmanager
    async def session(): yield db_session
    monkeypatch.setattr(pipeline, 'AsyncSessionLocal', session)
    monkeypatch.setattr(pipeline, '_resume_plan', AsyncMock(return_value=(False, False, False, False)))
    monkeypatch.setattr(pipeline, '_script_stage', AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(pipeline, 'publish_progress', AsyncMock())
    monkeypatch.setattr(scheduler, '_planned_stop_task_ids', {70001})
    with pipeline.kepu_execution(project.id, 70001):
        with pytest.raises(asyncio.CancelledError):
            await pipeline.run_pipeline(project.id)
    await db_session.refresh(project)
    assert project.status == ProjectStatus.SCRIPTING


@pytest.mark.parametrize('planned', [True, False])
async def test_cancellation_during_freeze_rolls_back_before_recovery(db_session, monkeypatch, planned):
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain='kepu', task_type='project_compose_only', status='leased')
    task.recovery_count = 2
    await db_session.commit()
    task_id = task.id
    entered = asyncio.Event()
    async def freeze(db, row):
        row.error_message = '本事务未完成，必须回滚'
        await db.flush()
        entered.set()
        await asyncio.Event().wait()
    @asynccontextmanager
    async def session():
        try:
            yield db_session
        except BaseException:
            await db_session.rollback()
            raise
    monkeypatch.setattr(executor, 'AsyncSessionLocal', session)
    monkeypatch.setattr(executor, 'freeze_for_task', freeze)
    monkeypatch.setattr(scheduler, '_scheduler_task', None)
    monkeypatch.setattr(scheduler, '_maintenance_task', None)
    monkeypatch.setattr(scheduler, '_running_jobs', {})
    monkeypatch.setattr(scheduler, '_cancel_jobs', {})
    job = asyncio.create_task(executor.execute_task_run(task_id))
    scheduler._running_jobs[task_id] = job
    await entered.wait()
    if planned:
        await scheduler.stop_scheduler()
    else:
        job.cancel()
        with pytest.raises(asyncio.CancelledError): await job
    await db_session.refresh(task)
    assert task.recovery_count == (2 if planned else 3)
    assert task.status == ('pending' if planned else 'failed')
    assert task.error_message != '本事务未完成，必须回滚'
