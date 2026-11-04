"""已知视频不重提，未知取消不退尽冻结，按providerID分别记实际成本。"""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import WalletLedger, UsageEvent
from app.services.ark import ArkGateway, TaskResult
from app.services.billing.context import billing_scope
from app.services.drama.billing_util import record_seedance_video_usage
from app.services.tasks.service import cancel_tasks_for_scope
from app.services.tasks.executor import _mark_cancelled
from app.services.video_recovery import VideoBillingPending
from tests.conftest import make_user, make_task


@pytest.mark.parametrize("known", [False, True])
async def test_timeout_queries_original_id_without_reposting(monkeypatch, known):
    gateway = ArkGateway(get_settings())
    gateway.gen_video_i2v = AsyncMock(return_value="accepted-123")
    gateway.wait_video = AsyncMock(side_effect=VideoBillingPending("poll_timeout", provider_task_id="accepted-123"))
    remember = AsyncMock()
    with pytest.raises(VideoBillingPending) as exc:
        await gateway.gen_and_wait_video("https://fake.test/image.png", "动作", 5, project_id=1, shot_no=1,
                                         provider_task_id="accepted-123" if known else None, on_task_created=remember)
    assert exc.value.provider_task_id == "accepted-123"
    assert gateway.gen_video_i2v.await_count == (0 if known else 1)
    assert remember.await_count == (0 if known else 1)
    gateway.wait_video.assert_awaited_once_with("accepted-123", project_id=1, shot_no=1)


@pytest.mark.parametrize("kind", ["voice", "video"])
async def test_real_scope_cancel_keeps_unknown_cost_freeze(db_session, kind):
    user = await make_user(db_session, balance_fen=0)
    user.frozen_fen = 100
    task = await make_task(db_session, user, domain="drama", task_type="voice_synthesis" if kind == "voice" else "asset_video",
                           status="running", billing_status="frozen", provider_task_id=None if kind == "voice" else "accepted-123")
    task.billing_estimate_fen = 100
    task.cancelable = False if kind == "voice" else True
    task.result_payload = {"voice_billing_reservation": True} if kind == "voice" else {}
    await db_session.commit()
    cancelled = await cancel_tasks_for_scope(db_session, user.id, task_type=task.task_type)
    assert [item.id for item in cancelled] == [task.id]
    await _mark_cancelled(db_session, cancelled[0])
    await db_session.refresh(task)
    await db_session.refresh(user)
    assert task.status == "awaiting_review"
    assert task.billing_status == "frozen"
    assert (user.balance_fen, user.frozen_fen) == (0, 100)
    assert not (await db_session.scalars(select(WalletLedger).where(WalletLedger.user_id == user.id))).all()


async def test_two_real_provider_ids_in_one_pipeline_each_record_once(db_session):
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type="project_pipeline", status="running")
    async with billing_scope(task.id):
        for provider in ["upstream-mirror-a", "upstream-mirror-b", "upstream-mirror-a"]:
            await record_seedance_video_usage(
                db_session, user_id=user.id, billing_key="seedance2:video1", model="seedance-2.5", domain="kepu",
                provider_task_id=provider, task_result=TaskResult(status="succeeded", total_tokens=1000, provider_task_id=provider),
            )
    assert len((await db_session.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).all()) == 2
