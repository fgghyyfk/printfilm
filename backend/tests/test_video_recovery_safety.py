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
    # 视频：合并后普通“已受理再取消”按线上规则计预扣费（见 test_policy_decisions）；
    # 只有显式标记了提交/轮询结果未知的任务取消时才保留冻结待核对。
    task.result_payload = ({"voice_billing_reservation": True} if kind == "voice"
                           else {"video_billing_pending": {"reason": "poll_timeout", "provider_task_id": "accepted-123"}})
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


async def test_failed_video_bills_actual_request_audio_and_model(db_session, monkeypatch):
    """未内置音频的真实失败响应不能因任务payload缺字段套用含音频费率。"""
    from app.services.video_recovery import VideoGenerationFailed
    from app.services.tasks.executor import _fail_task
    from app.services.tasks.service import get_task_for_runtime

    user = await make_user(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_video", status="running")
    await db_session.commit()
    task = await get_task_for_runtime(db_session, task.id)
    monkeypatch.setattr(get_settings(), "model_video", "seedance-2.5")
    gateway = ArkGateway(get_settings())
    gateway.gen_video_i2v = AsyncMock(return_value="original-failed")
    async def fail_after_config_changed(*args, **kwargs):
        monkeypatch.setattr(get_settings(), "model_video", "different-current-model")
        raise VideoGenerationFailed(TaskResult(status="failed", error="failed", total_tokens=1000, provider_task_id="original-failed"))
    gateway.wait_video = AsyncMock(side_effect=fail_after_config_changed)
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    with pytest.raises(VideoGenerationFailed) as caught:
        await gateway.gen_and_wait_video("/static/ref.png", "动作", 5, project_id=1, shot_no=1, generate_audio=False)
    exc = caught.value
    monkeypatch.setattr(get_settings(), "model_video", "different-current-model")
    await _fail_task(db_session, task, exc)
    usage = (await db_session.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).one()
    assert usage.billing_key == "seedance2:video1"
    assert usage.model == "seedance-2.5"


@pytest.mark.parametrize("rejected", [True, False])
async def test_submission_rejection_refunds_but_unknown_stays_frozen(db_session, rejected):
    from app.services.video_recovery import VideoSubmissionRejected
    from app.services.tasks.executor import _fail_task
    from app.services.tasks.service import get_task_for_runtime

    user = await make_user(db_session, balance_fen=0)
    user.frozen_fen = 100
    task = await make_task(db_session, user, domain="drama", task_type="fragment_video", status="running", billing_status="frozen")
    task.billing_estimate_fen = 100
    task.result_payload = {"video_submission_pending": True}
    await db_session.commit()
    task = await get_task_for_runtime(db_session, task.id)
    await _fail_task(db_session, task, VideoSubmissionRejected("400 InvalidParameter") if rejected else VideoBillingPending("submit_transport_unknown"))
    await db_session.refresh(user)
    assert task.status == ("failed" if rejected else "awaiting_review")
    assert task.billing_status == ("settled" if rejected else "frozen")
    assert user.frozen_fen == (0 if rejected else 100)
    assert user.balance_fen == (100 if rejected else 0)


@pytest.mark.parametrize("status", [400, 503])
async def test_gateway_typed_rejection_and_unknown_each_submit_once(monkeypatch, status):
    import httpx
    from app.services.video_recovery import VideoSubmissionRejected

    gateway = ArkGateway(get_settings())
    monkeypatch.setattr(ArkGateway, "mock", property(lambda _: False))
    monkeypatch.setattr(gateway, "_resolve_seedance_content_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *_: {})
    monkeypatch.setattr("app.services.drama.voice_reference_audio.validate_seedance_body_reference_audios", AsyncMock())
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(status, json={"error": {"code": "InvalidParameter", "message": "bad dimension"}})
    monkeypatch.setattr("app.services.ark.httpx.AsyncClient", Client)
    with pytest.raises(VideoSubmissionRejected if status == 400 else VideoBillingPending):
        await gateway.gen_video_seedance_body({"model": "seedance-2.5", "content": [], "duration": 5})
    assert len(calls) == 1
