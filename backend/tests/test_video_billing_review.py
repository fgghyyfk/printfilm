"""视频结果未知时保留冻结待核对，只能由管理员核对后结算（真实 PostgreSQL）。"""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.admin.tasks import admin_review_task_billing
from app.models import UsageEvent
from app.models_tasks import TaskEvent
from app.schemas_tasks import AdminTaskBillingReviewRequest
from app.services import ark as ark_module, video_recovery
from app.services.billing.context import billing_scope
from app.services.billing.review import resolve_billing_review
from app.services.billing.settlement import settle_task
from app.services.tasks import executor, poller
from app.services.tasks.recovery import recover_interrupted_task
from app.services.video_recovery import VideoBillingPending
from tests.conftest import make_task, make_user
from tests.test_tts_billing import _gateway, _http
from tests.tts_database import db_session  # noqa: F401 — 真实提交，独立连接才能看见

FROZEN = 100
BALANCE = 900


async def _frozen_task(db, *, domain="drama", task_type="fragment_video", status="running", provider=None):
    """创建已冻结 FROZEN 分的进行中任务。"""
    user = await make_user(db, balance_fen=BALANCE, frozen_fen=FROZEN)
    task = await make_task(db, user, domain=domain, task_type=task_type, status=status,
                           billing_status="frozen", provider_task_id=provider)
    task.billing_estimate_fen = FROZEN
    await db.flush()
    return user, task


def _assert_still_frozen(user, task):
    """钱没有动，任务待核对。"""
    assert task.status == "awaiting_review"
    assert task.billing_status == "frozen"
    assert task.finished_at is None and task.next_action_at is None
    assert int(user.balance_fen) == BALANCE and int(user.frozen_fen) == FROZEN


async def test_settle_keeps_freeze_when_video_result_unknown(db_session):
    """结算入口见到标记只转待核对，不退款。"""
    user, task = await _frozen_task(db_session)
    task.status = "failed"
    task.result_payload = {"video_billing_pending": {"reason": "poll_timeout"}}
    await settle_task(db_session, task.id)
    await db_session.refresh(user)
    _assert_still_frozen(user, task)
    assert task.error_code == "video_billing_pending"


async def test_marker_without_freeze_ends_as_normal_failure(db_session):
    """计费关闭时没有钱要保留，不能让任务卡在待核对。"""
    user = await make_user(db_session)
    task = await make_task(db_session, user, domain="drama", task_type="fragment_video",
                           status="failed", billing_status="skipped")
    task.result_payload = {"video_billing_pending": {"reason": "poll_timeout"}}
    await settle_task(db_session, task.id)
    assert task.status == "failed" and task.billing_status == "settled"


async def test_fail_task_detects_wrapped_pending_error(db_session):
    """调用方把异常包一层也要识别，失败投影照常写。"""
    user, task = await _frozen_task(db_session)
    try:
        try:
            raise VideoBillingPending("poll_timeout", provider_task_id="up-1")
        except VideoBillingPending as inner:
            raise RuntimeError(str(inner)) from inner
    except RuntimeError as wrapped:
        await executor._fail_task(db_session, await executor.get_task_for_runtime(db_session, task.id), wrapped)
    await db_session.refresh(user)
    await db_session.refresh(task)
    _assert_still_frozen(user, task)
    assert task.provider_task_id == "up-1"


@pytest.mark.parametrize("error,unknown", [
    (httpx.ReadTimeout("slow"), True),
    (httpx.WriteTimeout("slow"), True),
    (httpx.RemoteProtocolError("closed"), True),
    (httpx.ReadError("reset"), True),
    (httpx.ConnectTimeout("no route"), False),
    (httpx.ConnectError("refused"), False),
])
async def test_submit_failure_separates_unknown_from_not_sent(db_session, monkeypatch, tmp_path, error, unknown):
    """请求发出后没等到响应才算未知；没连上就是确定没建单，仍可普通失败退款。"""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(video_recovery, "AsyncSessionLocal", sessions)
    user, task = await _frozen_task(db_session)
    await db_session.commit()
    gateway = _gateway(monkeypatch, tmp_path)
    calls = []

    def handler(request):
        """记录请求次数并模拟传输层失败。"""
        calls.append(request.url.path)
        raise error
    _http(monkeypatch, handler)
    expected = VideoBillingPending if unknown else Exception
    async with billing_scope(task.id):
        with pytest.raises(expected) as caught:
            await gateway.gen_video_seedance_body(
                {"model": "seedance-2.5", "content": [{"type": "text", "text": "x"}], "duration": 5, "resolution": "480p"},
                project_id=1,
            )
    assert isinstance(caught.value, VideoBillingPending) is unknown
    assert len(calls) == 1, "结果未知时绝不能重新提交"
    await db_session.refresh(task)
    assert bool((task.result_payload or {}).get("video_billing_pending")) is unknown


async def test_poll_timeout_raises_pending_and_notes_task(db_session, monkeypatch, tmp_path):
    """轮询等到截止时间不再返回失败结果，并把标记写到任务上。"""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(video_recovery, "AsyncSessionLocal", sessions)
    user, task = await _frozen_task(db_session)
    await db_session.commit()
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(gateway.settings, "ark_video_poll_timeout", 0)
    async with billing_scope(task.id):
        with pytest.raises(VideoBillingPending):
            await gateway.wait_video_assets("up-9", project_id=1, shot_no=1)
    await db_session.refresh(task)
    assert task.result_payload["video_billing_pending"]["reason"] == "poll_timeout"
    assert task.provider_task_id == "up-9"


async def test_deferred_poller_timeout_keeps_freeze(db_session, monkeypatch):
    """后台轮询超时不再写“预扣已退回”。"""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(poller, "AsyncSessionLocal", sessions)
    user, task = await _frozen_task(db_session, domain="api", task_type="v1_video", status="awaiting_poll",
                                    provider="up-2")
    task.started_at = datetime.now(UTC) - timedelta(days=2)
    await db_session.commit()
    await poller._poll_one_ephemeral_task(task.id, now=datetime.now(UTC), timeout_sec=60)
    await db_session.refresh(user)
    await db_session.refresh(task)
    _assert_still_frozen(user, task)


async def test_unresumable_provider_interruption_keeps_freeze(db_session):
    """中断后已有任务号但无法跟进：费用未知，不能全额退。"""
    user, task = await _frozen_task(db_session, domain="kepu", task_type="project_pipeline", provider="up-3")
    await db_session.commit()
    assert await recover_interrupted_task(db_session, task.id) is True
    await db_session.refresh(user)
    await db_session.refresh(task)
    _assert_still_frozen(user, task)


async def _pending(db, *, key="video_billing_pending"):
    """创建一个待核对任务。"""
    user, task = await _frozen_task(db)
    task.result_payload = {key: {"reason": "poll_timeout"}, "keep": 1}
    await settle_task(db, task.id)
    return user, task


@pytest.mark.parametrize("key", ["video_billing_pending", "tts_billing_pending"])
async def test_review_full_refund_records_platform_cost(db_session, key):
    """失败不向用户收费：成本只进平台账，冻结全部退回。"""
    user, task = await _pending(db_session, key=key)
    await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=30, charge_fen=0, note="账单核对")
    await db_session.refresh(user)
    await db_session.refresh(task)
    assert task.status == "failed" and task.billing_status == "settled"
    assert task.error_code == "billing_review_resolved" and task.finished_at is not None
    assert int(user.balance_fen) == BALANCE + FROZEN and int(user.frozen_fen) == 0
    assert task.billing_charged_fen == 0
    assert key not in task.result_payload and task.result_payload["keep"] == 1
    row = (await db_session.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).one()
    assert row.cost_fen == 30 and row.charge_fen == 0 and row.settled is True


async def test_review_partial_charge_refunds_rest(db_session):
    """确认视频已交付时可收费，其余冻结退回。"""
    user, task = await _pending(db_session)
    await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=30, charge_fen=40, note="已交付")
    await db_session.refresh(user)
    await db_session.refresh(task)
    assert task.billing_charged_fen == 40
    assert int(user.balance_fen) == BALANCE + FROZEN - 40 and int(user.frozen_fen) == 0


async def test_review_rejects_charge_above_freeze_and_non_pending(db_session):
    """收费不能超预扣；已处理的任务不能再核对一次。"""
    _, task = await _pending(db_session)
    with pytest.raises(ValueError, match="预扣额"):
        await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=FROZEN + 1, note="x")
    await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=0, note="x")
    with pytest.raises(ValueError, match="待核对"):
        await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=0, note="x")


def _prior_usage(task, charge_fen):
    """核对前已记录、尚未结算的用量行。"""
    return UsageEvent(user_id=task.requested_by, task_run_id=task.id, charge_fen=charge_fen, cost_fen=charge_fen,
                      billing_key="llm_chat", domain=task.domain, provider="ark", model="", capability="llm",
                      estimated=False, settled=False, prompt_tokens=0, completion_tokens=0, total_tokens=0)


async def test_review_cap_counts_existing_unsettled_usage(db_session):
    """已有用量 + 本次收费合计不能超预扣，否则结算会多扣钱包。"""
    user, task = await _pending(db_session)
    db_session.add(_prior_usage(task, 60))
    await db_session.flush()
    with pytest.raises(ValueError, match="预扣额"):
        await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=41, note="x")
    await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=40, note="x")
    await db_session.refresh(user)
    await db_session.refresh(task)
    assert task.billing_charged_fen == FROZEN
    assert int(user.balance_fen) == BALANCE and int(user.frozen_fen) == 0


async def test_review_zero_charge_allowed_when_usage_already_exceeds_freeze(db_session):
    """已有用量已超预扣时仍能收费 0 结束待核对，不把任务卡死。"""
    _, task = await _pending(db_session)
    db_session.add(_prior_usage(task, FROZEN + 5))
    await db_session.flush()
    with pytest.raises(ValueError, match="预扣额"):
        await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=1, note="x")
    await resolve_billing_review(db_session, task.id, admin_id=1, cost_fen=0, charge_fen=0, note="x")
    await db_session.refresh(task)
    assert task.billing_status == "settled" and task.billing_charged_fen == FROZEN + 5


async def test_review_note_stays_out_of_user_visible_events(db_session):
    """用户能读任务事件，核对依据只能留在管理员可见的用量行。"""
    _, task = await _pending(db_session)
    await resolve_billing_review(db_session, task.id, admin_id=7, cost_fen=1, charge_fen=0, note="私密依据")
    events = (await db_session.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id))).all()
    assert "私密依据" not in " ".join(f"{e.message} {e.payload}" for e in events)
    row = (await db_session.scalars(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).one()
    assert "私密依据" in row.raw_usage_json and '"admin_id": 7' in row.raw_usage_json


async def test_review_endpoint_maps_errors(db_session):
    """接口把不存在和状态不符翻成 404/409，空白依据 422。"""
    admin = await make_user(db_session)
    body = AdminTaskBillingReviewRequest(cost_fen=0, charge_fen=0, note="依据")
    with pytest.raises(HTTPException) as missing:
        await admin_review_task_billing(999_999_999, body, admin=admin, db=db_session)
    assert missing.value.status_code == 404
    user, task = await _frozen_task(db_session)
    with pytest.raises(HTTPException) as wrong_state:
        await admin_review_task_billing(task.id, body, admin=admin, db=db_session)
    assert wrong_state.value.status_code == 409
    with pytest.raises(HTTPException) as blank:
        await admin_review_task_billing(
            task.id, AdminTaskBillingReviewRequest(note="   "), admin=admin, db=db_session,
        )
    assert blank.value.status_code == 422


def test_ark_module_exposes_helper():
    """网关直接依赖写标记的入口，避免循环导入回退。"""
    assert ark_module.raise_video_billing_pending is video_recovery.raise_video_billing_pending
