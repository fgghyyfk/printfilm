"""逐阶段自动接续的原子结算、幂等与跨连接取消边界。"""
import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Project, ProjectStatus, Shot, UsageEvent
from app.models_tasks import TaskRun
from app.services.tasks.executor import _complete_task
from app.services.tasks.service import cancel_task_for_user, get_task_for_runtime
from tests.conftest import make_task
from tests.test_kepu_task_recovery import seed_project
from tests.tts_database import db_session


async def auto_parent(db, *, spent=0):
    user, project, shot = await seed_project(db, status=ProjectStatus.SCRIPT_READY)
    project.auto_advance, project.auto_chain_id = True, "chain-a"
    project.final_video_url = None
    shot.image_url = None
    user.balance_fen, user.frozen_fen = (0 if spent else 100_000), 100
    task = await make_task(db, user, domain="kepu", task_type="project_pipeline", status="running", billing_status="frozen")
    task.project_id, task.billing_estimate_fen = project.id, 100
    task.payload = {"phase": "script", "auto_chain_id": "chain-a"}
    if spent:
        db.add(UsageEvent(user_id=user.id, task_run_id=task.id, billing_key="llm_chat", model="mock", charge_fen=spent))
    await db.commit()
    return user, project, task


async def test_auto_completion_enqueues_once_and_parent_settles(db_session):
    user, project, task = await auto_parent(db_session)
    task = await get_task_for_runtime(db_session, task.id)
    await _complete_task(db_session, task, {"ok": True})
    child_id = task.result_payload["next_task_id"]
    await _complete_task(db_session, task, {"ok": True})
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id == project.id)) == 2
    child = await db_session.get(TaskRun, child_id)
    assert task.billing_status == "settled"
    assert child.payload["phase"] == "assets" and child.billing_status == "none"
    assert child.payload["auto_chain_id"] == "chain-a"


async def test_insufficient_next_stage_preserves_parent_cost_and_manuscript(db_session):
    user, project, task = await auto_parent(db_session, spent=100)
    task = await get_task_for_runtime(db_session, task.id)
    await _complete_task(db_session, task, {"ok": True})
    await db_session.refresh(project)
    await db_session.refresh(user)
    assert (task.status, task.billing_status, task.billing_charged_fen) == ("succeeded", "settled", 100)
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id == project.id)) == 1
    assert await db_session.scalar(select(Shot.id).where(Shot.project_id == project.id))
    assert project.auto_chain_id is None and "余额不足" in task.result_payload["auto_paused"]
    assert (user.balance_fen, user.frozen_fen) == (0, 0)


async def test_cancel_after_transition_reaches_new_child_and_duplicate_complete_is_safe(db_session, monkeypatch):
    """旧父任务的取消与收尾交错，项目→任务锁让刚入队的子任务也停止。"""
    from app.models import User
    from app.services.tasks import service
    from app.services.billing import newapi_bill

    user, project, task = await auto_parent(db_session)
    uid, pid, tid = user.id, project.id, task.id
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    real_create = service.create_task

    async def gated_create(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return await real_create(*args, **kwargs)

    async def logs_outside_locks(events):
        async with sessions() as probe:
            assert await probe.scalar(select(Project.id).where(Project.id == pid).with_for_update(nowait=True)) == pid
            assert await probe.scalar(select(TaskRun.id).where(TaskRun.id == tid).with_for_update(nowait=True)) == tid
        return []
    monkeypatch.setattr(newapi_bill, "prepare_newapi_bill_logs", logs_outside_locks)
    monkeypatch.setattr(service, "create_task", gated_create)

    async def complete():
        async with sessions() as db:
            await _complete_task(db, await get_task_for_runtime(db, tid), {"ok": True})
    async def cancel():
        async with sessions() as db:
            await cancel_task_for_user(db, await db.get(User, uid), tid)

    completing = asyncio.create_task(complete())
    await asyncio.wait_for(entered.wait(), 5)
    cancelling = asyncio.create_task(cancel())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(completing, cancelling), 10)
    await complete()
    async with sessions() as db:
        parent = await db.get(TaskRun, tid)
        child = await db.get(TaskRun, parent.result_payload["next_task_id"])
        assert child.status == "cancel_requested"
        assert child.cancel_requested
        assert (await db.get(Project, pid)).auto_chain_id is None
        assert await db.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id == pid)) == 2


@pytest.mark.parametrize("mode,expected", [("full", ["script", "assets", "videos", "compose"]),
                                          ("image_text", ["script", "assets", "compose"])])
async def test_each_auto_stage_freezes_and_settles_separately(db_session, monkeypatch, mode, expected):
    from app.models import WalletLedger
    from app.services.billing.settlement import freeze_for_task

    user, project, task = await auto_parent(db_session)
    project.pipeline_mode = mode
    user.frozen_fen = 0
    task.billing_status = "none"
    await db_session.commit()
    # 模拟外部配音已产出；这里只验证真实阶段解析和独立钱包流程。
    monkeypatch.setattr("app.services.kepu_stages.project_audio_ready", lambda _: True)
    phases = []
    while True:
        task = await get_task_for_runtime(db_session, task.id)
        phases.append(task.payload["phase"])
        await freeze_for_task(db_session, task)
        task.status = "running"
        if phases[-1] == "assets":
            shot = await db_session.scalar(select(Shot).where(Shot.project_id == project.id))
            shot.image_url = "/static/image.png"
        elif phases[-1] == "videos":
            shot = await db_session.scalar(select(Shot).where(Shot.project_id == project.id))
            shot.video_url = "/static/video.mp4"
        elif phases[-1] == "compose":
            project.status, project.final_video_url = ProjectStatus.DONE, "/static/final.mp4"
        await db_session.commit()
        await _complete_task(db_session, task, {"ok": True})
        assert task.billing_status == "settled"
        next_id = task.result_payload.get("next_task_id")
        if not next_id:
            assert task.result_payload.get("auto_finished")
            break
        task = await db_session.get(TaskRun, next_id)
    assert phases == expected
    assert await db_session.scalar(select(func.count()).select_from(WalletLedger).where(
        WalletLedger.user_id == user.id, WalletLedger.kind == "freeze")) == len(expected)


async def test_disabled_billing_auto_does_not_need_price_service(db_session, monkeypatch):
    from app.config import get_settings
    from unittest.mock import AsyncMock

    user, project, task = await auto_parent(db_session)
    monkeypatch.setattr(get_settings(), "billing_enabled", False)
    user.balance_fen = user.frozen_fen = 0
    task.billing_status = "skipped"
    await db_session.commit()
    quote = AsyncMock(side_effect=AssertionError("关闭计费不得请求价目"))
    monkeypatch.setattr("app.services.tokenfree_pricing.ensure_official_rates", quote)
    await _complete_task(db_session, await get_task_for_runtime(db_session, task.id), {"ok": True})
    child = await db_session.get(TaskRun, task.result_payload["next_task_id"])
    assert child.billing_estimate_fen == 0 and child.status == "pending"
    quote.assert_not_awaited()
