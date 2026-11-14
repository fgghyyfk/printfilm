"""TokenFree 计价表达式与 New API 消费日志对账。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import get_settings
from app.services.billing.newapi_bill import apply_log_quota, select_consume_log
from app.services.billingexpr import expression_usd, quote_verified_charge_fen
from app.services.tokenfree_pricing import (
    charge_fen_official_image,
    charge_fen_official_video,
    parse_pricing_payload,
    set_cached_rates,
)

_SEEDANCE_25 = (
    'u("input_video_seconds") > 0 ? '
    '(u("resolution") == "480p" ? tier("480p", u("seconds") * 0.1275) '
    ': tier("720p", u("seconds") * 0.285)) : '
    '(u("resolution") == "480p" ? tier("480p", u("seconds") * 0.21) : '
    'u("resolution") == "720p" ? tier("720p", u("seconds") * 0.4725) : '
    'tier("std", u("seconds") * 0.855))'
)


def _rates() -> None:
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    payload = {
        "data": [
            {
                "model_name": "seedance-2-5",
                "quota_type": 0,
                "model_ratio": 37.5,
                "completion_ratio": 1,
                "model_price": 0,
                "billing_mode": "tiered_expr",
                "billing_expr": _SEEDANCE_25,
            },
            {
                "model_name": "gpt-image-2",
                "quota_type": 1,
                "model_price": 0.075,
                "model_ratio": 0,
                "completion_ratio": 0,
            },
            {
                "model_name": "kimi-k2.6",
                "quota_type": 0,
                "model_price": 0,
                "model_ratio": 0.5,
                "completion_ratio": 4,
            },
        ]
    }
    set_cached_rates(parse_pricing_payload(payload, settings))


def test_seedance_expr_is_usd_per_second_not_placeholder_ratio() -> None:
    """480p 5 秒、无参考视频 = 5 × $0.21。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    usd = expression_usd(
        _SEEDANCE_25,
        tokens={},
        usage={"seconds": 5, "resolution": "480p", "input_video_seconds": 0},
    )
    assert usd is not None
    assert abs(usd - 1.05) < 1e-9
    _rates()
    try:
        # 1.05 USD × 7 = 7.35 元 = 735 分
        assert charge_fen_official_video(5, settings, model="seedance-2-5", resolution="480p") == 735
    finally:
        set_cached_rates(None)


def test_video_charge_uses_resolution_and_duration_not_tokens() -> None:
    """同一模型：清晰度越高越贵，时长加倍则费用加倍；token 数不参与。"""
    from app.services.billing.pricing import charge_fen_for_usage

    settings = get_settings()
    settings.billing_usd_cny = 7.0
    _rates()
    try:
        low, _, _ = charge_fen_for_usage(
            100_000,
            "seedance2:video0",
            raw_usage={"usage": {"seconds": 5, "resolution": "480p", "total_tokens": 100_000}},
            model="seedance-2-5",
            settings=settings,
        )
        mid, _, _ = charge_fen_for_usage(
            1,
            "seedance2:video0",
            raw_usage={"usage": {"seconds": 5, "resolution": "720p"}},
            model="seedance-2-5",
            settings=settings,
        )
        long, _, _ = charge_fen_for_usage(
            1,
            "seedance2:video0",
            raw_usage={"usage": {"seconds": 10, "resolution": "720p"}},
            model="seedance-2-5",
            settings=settings,
        )
        # 5s×$0.21=735 分；5s×$0.4725=1654 分；10s 为两倍
        assert low == 735
        assert mid == 1654
        assert long == mid * 2
    finally:
        set_cached_rates(None)


def test_expr_without_seconds_does_not_charge_zero() -> None:
    usd = expression_usd(_SEEDANCE_25, tokens={}, usage={"resolution": "720p"})
    assert usd is None


def test_seedance_mini_token_expr_not_vendor_sec_table() -> None:
    """TokenFree mini 现价按 u(tokens)；不得静默落到偏低本站秒价表（8s 720p≠¥4）。"""
    from app.services.billing.pricing import charge_fen_for_usage

    settings = get_settings()
    settings.billing_usd_cny = 7.0
    settings.billing_markup = 1.0
    settings.billing_est_seedance_tokens_per_sec = 32_000
    expr = (
        'u("video_input") == "video" ? '
        'tier("video", u("tokens") * (14 / 7.3 * 1.5) / 1000000) : '
        'tier("base", u("tokens") * (23 / 7.3 * 1.5) / 1000000)'
    )
    payload = {
        "data": [
            {
                "model_name": "seedance-2.0-mini",
                "quota_type": 0,
                "model_ratio": 37.5,
                "completion_ratio": 1,
                "model_price": 0,
                "billing_mode": "tiered_expr",
                "billing_expr": expr,
            }
        ]
    }
    set_cached_rates(parse_pricing_payload(payload, settings))
    try:
        # 8s × 32000 tok/s = 256000 → 但 record 用 8*32000=256000；本测用事件里的 320000
        fen, _, _ = charge_fen_for_usage(
            320_000,
            "seedance2:video0",
            raw_usage={
                "usage": {
                    "seconds": 8.0,
                    "resolution": "720p",
                    "tokens": 320_000,
                    "input_video_seconds": 0,
                }
            },
            model="seedance-2.0-mini",
            settings=settings,
        )
        # 320000 * (23/7.3*1.5)/1e6 USD × 7 = ¥10.59 → 1059 分；绝不是秒价表 400
        assert fen == 1059
        # 无显式 tokens 时 720p 按像素估 22k/秒 → 8×22000=176000
        auto = charge_fen_official_video(
            8.0, settings, model="seedance-2.0-mini", resolution="720p"
        )
        assert auto in {582, 583}
        assert fen > 400
        assert auto > 400
    finally:
        set_cached_rates(None)


def test_gpt_image_uses_tokenfree_per_call_not_kie() -> None:
    """价目 model_price=$0.075 时按账单规则，不再用 Kie 35 分。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    settings.billing_markup = 1.0
    _rates()
    try:
        # 0.075 × 7 × 100 = 52.5 → 53 分
        assert charge_fen_official_image(settings, model="gpt-image-2", size="2K") == 53
    finally:
        set_cached_rates(None)


def test_token_ratio_uses_prompt_and_completion() -> None:
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    _rates()
    try:
        # model_ratio 0.5 → 输入 $1/百万；completion_ratio 4 → 输出 $4/百万
        # 1e6 输入 = $1 × 7 = 700 分
        fen = quote_verified_charge_fen(
            model="kimi-k2.6",
            prompt=1_000_000,
            completion=0,
            settings=settings,
        )
        assert fen == 700
    finally:
        set_cached_rates(None)


class _Event:
    def __init__(self, **kwargs: object) -> None:
        self.model = kwargs.get("model", "")
        self.prompt_tokens = kwargs.get("prompt_tokens", 0)
        self.completion_tokens = kwargs.get("completion_tokens", 0)
        self.raw_usage_json = kwargs.get("raw_usage_json", "{}")
        self.cost_fen = 1
        self.charge_fen = 1
        self.estimated = True
        self.created_at = datetime(2026, 10, 13, tzinfo=UTC)


def test_select_consume_log_matches_tokens_and_skips_ambiguous() -> None:
    event = _Event(model="kimi-k2.6", prompt_tokens=10, completion_tokens=20)
    logs = [
        {"id": 1, "model_name": "kimi-k2.6", "quota": 100, "prompt_tokens": 1, "completion_tokens": 1},
        {"id": 2, "model_name": "kimi-k2.6", "quota": 250, "prompt_tokens": 10, "completion_tokens": 20},
    ]
    chosen = select_consume_log(event, logs, set())
    assert chosen is not None
    assert chosen["id"] == 2
    ambiguous = _Event(model="kimi-k2.6")
    assert select_consume_log(ambiguous, logs, set()) is None


def test_apply_log_quota_overwrites_charge() -> None:
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    event = _Event(model="seedance-2-5", raw_usage_json='{"provider_task_id": "task_1"}')
    log = {"id": 9, "model_name": "seedance-2-5", "quota": 500_000, "content": "task_1"}
    assert select_consume_log(event, [log], set())["id"] == 9
    assert apply_log_quota(event, log) is True
    # 500000 quota = 1 USD × 7 = 700 分
    assert event.charge_fen == 700
    assert event.estimated is False
    assert "newapi_log" in (event.raw_usage_json or "")


_GPT_IMAGE_25 = (
    'u("resolution") == "4K" ? 0.08 * 1.5 : (u("resolution") == "2K" ? 0.05 * 1.5 : 0.03 * 1.5)'
)


def test_expr_string_compare_ignores_case() -> None:
    """上游写 "4K"，本地事实归一成小写 "4k" 也要命中同一档。"""
    for res, usd in (("1k", 0.045), ("2k", 0.075), ("4K", 0.12), ("4k", 0.12)):
        got = expression_usd(_GPT_IMAGE_25, tokens={}, usage={"resolution": res})
        assert got is not None and abs(got - usd) < 1e-9


def test_gpt_image_25_charge_tiers_by_resolution() -> None:
    """gpt-image-2.5 按 1K/2K/4K 分档预估，不再全部落到 1K 档。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    settings.billing_markup = 1.0
    payload = {
        "data": [
            {
                "model_name": "gpt-image-2.5",
                "quota_type": 1,
                "model_price": 0,
                "model_ratio": 0,
                "completion_ratio": 0,
                "billing_mode": "tiered_expr",
                "billing_expr": _GPT_IMAGE_25,
            }
        ]
    }
    set_cached_rates(parse_pricing_payload(payload, settings))
    try:
        fens = [charge_fen_official_image(settings, model="gpt-image-2.5", size=s) for s in ("1K", "2K", "4K")]
        # $0.045 / $0.075 / $0.12 × 7
        assert fens == [32, 53, 84]
    finally:
        set_cached_rates(None)
