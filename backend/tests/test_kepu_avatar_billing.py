"""数字人计费：改图张价、全自动/批量估价、场景图数量、限频与 429 映射、用量行绑定会话。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import kepu_avatar_billing as kb
from app.services.billing.http import http_exception_for_value_error


def _settings(**kw):
    """估价用的最小配置。"""
    base = dict(billing_usd_cny=7.2, avatar_price_fen_per_sec=0, billing_estimate_buffer=1.0,
                billing_est_llm_tokens=0, billing_markup=1.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _project(**kw):
    """不带分镜的项目替身。"""
    base = dict(avatar_scene_mode="per_segment", avatar_look_style="cartoon3d", avatar_image_model="gpt-image-2",
                voice_id="zh_female_cancan_uranus_bigtts", source_type="theme", source_text="主题")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _flat_pricing(monkeypatch):
    """不加价、写稿按 0 估，便于核对金额。"""
    from app.services.billing import pricing
    from app.services import tokenfree_pricing

    monkeypatch.setattr(pricing, "user_charge_fen", lambda cost, s=None: int(cost))
    monkeypatch.setattr(tokenfree_pricing, "charge_fen_official_llm", lambda tokens, s=None: 0)
    monkeypatch.setattr(kb, "get_settings", lambda: _settings())


def test_image_cost_by_model():
    """gpt-image-2 约 ¥1.89/张，Gemini 约 ¥0.51/张，未知模型按默认价。"""
    s = _settings()
    assert kb.image_cost_fen("gpt-image-2", s) == 189
    assert kb.image_cost_fen("gemini-3.1-flash-image", s) == 51
    assert kb.image_cost_fen("unknown-model", s) == 58


def test_scene_image_count_rules():
    """保持原样 + 全片固定不画；全片固定画 1 张；每段换场景每段 1 张。"""
    assert kb.scene_image_count(_project(avatar_scene_mode="fixed", avatar_look_style="original"), 5) == 0
    assert kb.scene_image_count(_project(avatar_scene_mode="fixed"), 5) == 1
    assert kb.scene_image_count(_project(), 5) == 5


def test_estimate_auto_scales_with_script_and_gpu_price():
    """估价随文稿长度增长：60 秒 → 5 段场景图 + 60 秒 GPU。"""
    s = _settings(avatar_price_fen_per_sec=2)
    fen = kb.estimate_auto_fen(_project(), s, chars=270)
    assert fen == 5 * 189 + 60 * 2
    fixed_original = _project(avatar_scene_mode="fixed", avatar_look_style="original")
    assert kb.estimate_auto_fen(fixed_original, s, chars=270) == 60 * 2


def test_estimate_auto_counts_openai_voice():
    """OpenAI 音色按字计入配音费用。"""
    s = _settings()
    base = kb.estimate_auto_fen(_project(avatar_scene_mode="fixed", avatar_look_style="original"), s, chars=450)
    oa = kb.estimate_auto_fen(_project(avatar_scene_mode="fixed", avatar_look_style="original",
                                       voice_id="openai_shimmer"), s, chars=450)
    assert base == 1  # 其它都为 0 时至少 1 分
    assert oa == kb.speech_cost_fen(450, s) == 10


def test_estimate_batch_uses_script_length():
    """口播稿按字数估，选题按 60 秒估。"""
    src = _project(avatar_scene_mode="fixed", avatar_look_style="original")
    short = kb.estimate_batch_fen(src, ["一" * 45], "script")
    theme = kb.estimate_batch_fen(src, ["选题"], "theme")
    assert short <= theme


@pytest.mark.asyncio
async def test_image_quota_busy_user_gets_429():
    """同一用户上一张没完成时直接拒绝，并映射成 429。"""
    kb._image_busy_users.add(99)
    try:
        with pytest.raises(kb.ImageRateLimited) as info:
            await kb.check_image_quota(AsyncMock(), 99)
    finally:
        kb._image_busy_users.discard(99)
    assert http_exception_for_value_error(info.value).status_code == 429


@pytest.mark.asyncio
async def test_image_quota_hourly_limit():
    """一小时内达到上限后拒绝。"""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=kb.IMAGE_HOURLY_LIMIT)
    with pytest.raises(kb.ImageRateLimited):
        await kb.check_image_quota(db, 1)
    db.scalar = AsyncMock(return_value=0)
    await kb.check_image_quota(db, 1)


def test_balance_error_still_402():
    """余额不足仍是 402。"""
    assert http_exception_for_value_error(ValueError("余额不足：需要 ¥1.00")).status_code == 402


@pytest.mark.asyncio
async def test_record_avatar_ai_uses_bound_session(monkeypatch):
    """请求内绑定会话时，用量行写进同一个会话（任务还没提交，别的会话看不到）。"""
    from app.services import billing
    from app.services.billing import context

    task = SimpleNamespace(requested_by=5, project_id=8, shot_id=None, domain="kepu")
    db = AsyncMock()
    db.get = AsyncMock(return_value=task)
    record = AsyncMock()
    monkeypatch.setattr(billing, "record_line", record)
    context.set_current_task_run_id(123)
    try:
        with kb.bind_session(db):
            await kb.record_avatar_ai("avatar_image", model="gpt-image-2", cost_fen=189)
    finally:
        context.set_current_task_run_id(None)
    assert record.await_args.args[0] is db
    assert record.await_args.kwargs["billing_key"] == "avatar_image"
    assert record.await_args.kwargs["raw"]["cost_fen"] == 189
    assert record.await_args.kwargs["user_id"] == 5


@pytest.mark.asyncio
async def test_record_avatar_ai_outside_task_is_skipped(monkeypatch):
    """不在任务里（没法结算）时不写用量行。"""
    from app.services import billing

    record = AsyncMock()
    monkeypatch.setattr(billing, "record_line", record)
    await kb.record_avatar_ai("avatar_image", model="gpt-image-2", cost_fen=189)
    record.assert_not_awaited()
