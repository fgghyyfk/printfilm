"""TokenFree 官方价目解析与计费预估。"""

from __future__ import annotations

from app.config import get_settings
from app.services.tokenfree_pricing import (
    OfficialRate,
    charge_fen_official_image,
    charge_fen_official_llm,
    charge_fen_official_video,
    lookup_rate,
    parse_pricing_payload,
    set_cached_rates,
    video_catalog_id,
)


def _sample_payload() -> dict:
    return {
        "success": True,
        "data": [
            {
                "model_name": "gpt-image-2-5",
                "quota_type": 1,
                "model_price": 0.625,
                "model_ratio": 0,
                "completion_ratio": 0,
            },
            {
                "model_name": "kimi-k2.6",
                "quota_type": 0,
                "model_price": 0,
                "model_ratio": 0.727612,
                "completion_ratio": 4.15385,
            },
            {
                "model_name": "seedance-2-5",
                "quota_type": 0,
                "model_price": 0,
                "model_ratio": 37.5,
                "completion_ratio": 1,
            },
        ],
    }


def test_parse_pricing_payload_per_call_and_token() -> None:
    """按张价与 token 价按 7 汇率折人民币。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    rates = parse_pricing_payload(_sample_payload(), settings)
    image = rates["gpt-image-2-5"]
    assert image.billing == "per_call"
    assert image.cny_per_call == 4.375
    # 点分 id 应能命中价目里的连字符键
    assert lookup_rate("gpt-image-2.5", rates) is image
    assert lookup_rate("gpt-image-2-5", rates) is image
    llm = rates["kimi-k2.6"]
    assert llm.billing == "token"
    assert abs(llm.cny_in_per_1m - 10.1866) < 0.01
    video = rates["seedance-2-5"]
    assert video.placeholder is True


def test_capability_from_tokenfree_pricing_tags() -> None:
    """价目 tags / endpoints 归类：MiniMax-H3→video，chat→无强信号。"""
    from app.services.tokenfree_pricing import (
        capability_from_tokenfree_meta,
        lookup_tokenfree_capability,
        parse_pricing_payload,
        set_cached_rates,
    )

    assert (
        capability_from_tokenfree_meta(
            "Hailuo,Video,Text to Video,Image to Video",
            ["openai", "openai-video"],
        )
        == "video"
    )
    assert capability_from_tokenfree_meta("OpenAI,Chat", ["openai"]) is None
    assert capability_from_tokenfree_meta("Google,TTS,Audio,Text to Speech", ["openai"]) == "audio"
    assert (
        capability_from_tokenfree_meta("ByteDance,Text to Image", ["openai"]) == "image"
    )

    rates = parse_pricing_payload(
        {
            "data": [
                {
                    "model_name": "MiniMax-H3",
                    "tags": "Hailuo,Video,Text to Video,Image to Video",
                    "supported_endpoint_types": ["openai", "openai-video"],
                    "quota_type": 0,
                    "model_ratio": 37.5,
                    "completion_ratio": 1,
                    "model_price": 0,
                },
                {
                    "model_name": "kimi-k2.6",
                    "tags": "Chat",
                    "supported_endpoint_types": ["openai"],
                    "quota_type": 0,
                    "model_ratio": 0.7,
                    "completion_ratio": 1,
                    "model_price": 0,
                },
            ]
        }
    )
    set_cached_rates(rates)
    try:
        assert lookup_tokenfree_capability("MiniMax-H3") == "video"
        assert rates["MiniMax-H3"].capability == "video"
        assert lookup_tokenfree_capability("kimi-k2.6") is None
        assert rates["kimi-k2.6"].capability is None
    finally:
        set_cached_rates(None)


def test_openai_protocol_channel_sync_heals_minimax_h3_to_video() -> None:
    """TokenFree openai 协议不再整渠道路径判 text；误标 text 的 H3 同步时纠正。"""
    from app.schemas_routing import LogicalModel, LogicalModelBinding, SystemModelChannel
    from app.services.model_routing_config import synchronize_logical_models_with_channels
    from app.services.tokenfree_pricing import parse_pricing_payload, set_cached_rates

    set_cached_rates(
        parse_pricing_payload(
            {
                "data": [
                    {
                        "model_name": "MiniMax-H3",
                        "tags": "Hailuo,Video,Text to Video,Image to Video",
                        "supported_endpoint_types": ["openai", "openai-video"],
                        "quota_type": 0,
                        "model_ratio": 37.5,
                        "completion_ratio": 1,
                        "model_price": 0,
                    }
                ]
            }
        )
    )
    try:
        channels = [
            SystemModelChannel(
                id="tokenfree",
                name="TokenFree",
                base_url="https://www.tokenfree.com/v1",
                api_key="sk-test",
                has_api_key=True,
                protocol="openai",
                models=["MiniMax-H3", "kimi-k2.6"],
                enabled=True,
            )
        ]
        stale = [
            LogicalModel(
                id="MiniMax-H3",
                name="MiniMax-H3",
                capability="text",
                enabled=True,
                bindings=[
                    LogicalModelBinding(
                        id="tokenfree:MiniMax-H3",
                        channel_id="tokenfree",
                        upstream_model="MiniMax-H3",
                        enabled=True,
                        priority=1,
                    )
                ],
            )
        ]
        synced = synchronize_logical_models_with_channels(stale, channels)
        by_id = {m.id: m for m in synced}
        assert by_id["MiniMax-H3"].capability == "video"
        assert by_id["kimi-k2.6"].capability == "text"
    finally:
        set_cached_rates(None)


def test_charge_official_image_uses_per_call() -> None:
    """有官方按张价时，Seedream 走价目而非 Kie 积分档。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    settings.billing_markup = 1.0
    settings.billing_seedream_per_m = 8.0
    settings.billing_est_seedream_tokens = 45_000
    payload = _sample_payload()
    payload["data"].append(
        {
            "model_name": "seedream-5-0-pro",
            "quota_type": 1,
            "model_price": 0.04,
            "model_ratio": 0,
            "completion_ratio": 0,
        }
    )
    rates = parse_pricing_payload(payload, settings)
    set_cached_rates(rates)
    try:
        charge = charge_fen_official_image(settings, model="seedream-5-0-pro")
        # $0.04 × 7 = ¥0.28 → 28 分
        assert charge == 28
        # gpt-image 仍走 Kie
        assert charge_fen_official_image(settings, model="gpt-image-2", size="2K") == 35
    finally:
        set_cached_rates(None)


def test_charge_official_image_seedream_falls_back_without_rate() -> None:
    """Seedream 无价目时用 Kie 2K 档保守保底。"""
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.0
    set_cached_rates(None)
    assert charge_fen_official_image(settings, model="seedream-5-0-pro") == 35


def test_resolve_billing_image_size_compact_forces_1k() -> None:
    """紧凑图模计费与生成侧一致：3K → 1K。"""
    from app.services.tokenfree_pricing import resolve_billing_image_size

    settings = get_settings()
    assert resolve_billing_image_size(settings, model="z-image-turbo", size="3K") == "1K"
    assert resolve_billing_image_size(settings, model="seedream-5-0-pro", size="3K") == "2K"


def test_charge_official_image_falls_back_without_cache() -> None:
    """无价目缓存时按 2K sunburst 积分档保底，不再退回 8 元/百万 token。"""
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.0
    settings.billing_seedream_per_m = 8.0
    settings.billing_est_seedream_tokens = 45_000
    set_cached_rates(None)
    # 2K sunburst 10 积分 × 3.5 分 = 35 分（$0.05）
    assert charge_fen_official_image(settings) == 35


def test_charge_official_image_uses_sunburst_size_tiers() -> None:
    """Kie sunburst 1K/2K/4K 分档，不误用 OpenAI $0.625。"""
    from app.services.tokenfree_pricing import (
        kie_sunburst_credits_for_size,
        kie_sunburst_usd_for_size,
        resolve_billing_image_size,
    )

    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.0
    settings.billing_kie_fen_per_credit = 3.5
    set_cached_rates(None)
    assert kie_sunburst_usd_for_size("1K") == 0.03
    assert kie_sunburst_usd_for_size("2K") == 0.05
    assert kie_sunburst_usd_for_size("3K") == 0.08
    assert kie_sunburst_credits_for_size("1K") == 6
    assert kie_sunburst_credits_for_size("2K") == 10
    assert kie_sunburst_credits_for_size("4K") == 16
    assert charge_fen_official_image(settings, model="gpt-image-2", size="1K") == 21
    assert charge_fen_official_image(settings, model="gpt-image-2", size="2K") == 35
    # GPT Image 2 的 4K 钳到 2K；2.5 保留 4K
    assert charge_fen_official_image(settings, model="gpt-image-2", size="4K") == 35
    assert kie_sunburst_credits_for_size("4K") == 16
    assert resolve_billing_image_size(settings, model="seedream-5-0-pro", size="3K") == "2K"
    assert resolve_billing_image_size(settings, model="gpt-image-2", size="4K") == "2K"
    assert resolve_billing_image_size(settings, model="gpt-image-2.5", size="4K") == "4K"


def test_charge_official_video_720p() -> None:
    """2.5 对齐 TF：刊例×1.5×像素 tok；1080p 高于 720p。"""
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.3
    set_cached_rates(None)
    fen_480 = charge_fen_official_video(5.0, settings, model="seedance-2-5", resolution="480p")
    fen_720 = charge_fen_official_video(5.0, settings, model="seedance-2-5", resolution="720p")
    fen_1080 = charge_fen_official_video(5.0, settings, model="seedance-2-5", resolution="1080p")
    # 5s：10k/22k/49k tok × (70|77)×1.5 /1e6
    assert fen_480 == 525
    assert fen_720 == 1155
    assert fen_1080 == 2830
    assert fen_1080 > fen_720 > fen_480


def test_tf_seedance_25_expr_matches_channel_export() -> None:
    """固化的 billing_expr 与 TF Ark 渠道导出一致：245025 tok·1080p≈¥28.31。"""
    from app.services.tokenfree_pricing import (
        SEEDANCE_TF_BILLING_EXPR,
        builtin_seedance_official_rates,
        merge_seedance_tf_rates,
    )

    expr = SEEDANCE_TF_BILLING_EXPR["seedance-2.5"]
    assert 'u("tokens") * (77 / 7.3 * 1.5)' in expr
    assert 'u("tokens") * (70 / 7.3 * 1.5)' in expr
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.3
    set_cached_rates(merge_seedance_tf_rates(builtin_seedance_official_rates()))
    try:
        fen = charge_fen_official_video(
            5.0,
            settings,
            model="seedance-2.5",
            resolution="1080p",
            tokens=245_025,
        )
        assert fen == 2831
    finally:
        set_cached_rates(None)


def test_charge_official_video_mini_aliases_to_2_0() -> None:
    """Mini / mimi 规范到 2.0，与 2.0 同价。"""
    from app.services.tokenfree_pricing import canonicalize_channel_model_id

    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.3
    set_cached_rates(None)
    assert canonicalize_channel_model_id("seedance-2.0-mimi") == "seedance-2.0"
    mini = charge_fen_official_video(5.0, settings, model="seedance-2-0-mini", resolution="480p")
    full = charge_fen_official_video(5.0, settings, model="seedance-2-0", resolution="480p")
    assert mini == full == 345


def test_charge_official_video_minimax_not_seedance_fallback() -> None:
    """MiniMax 走自有秒价，不误落到 seedance-2-5。"""
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.3
    set_cached_rates(None)
    fen = charge_fen_official_video(5.0, settings, model="MiniMax-H3", resolution="720p")
    # 5s × $0.08/秒 × 7.3 = ¥2.92 → 292 分
    assert fen == 292
    seedance = charge_fen_official_video(5.0, settings, model="seedance-2-5", resolution="720p")
    assert fen != seedance


def test_charge_official_video_uses_vendor_sec_not_placeholder() -> None:
    """无可用表达式时走本站兜底秒价（≥¥1.5/秒），不用 37.5 占位倍率。"""
    settings = get_settings()
    settings.billing_markup = 1.0
    settings.billing_usd_cny = 7.3
    rates = parse_pricing_payload(_sample_payload(), settings)
    set_cached_rates(rates)
    try:
        charge = charge_fen_official_video(5.0, settings, model="seedance-2-5", resolution="480p")
        # TF 对齐兜底：5s×10k tok×70×1.5 → ¥5.25 → 525 分
        assert charge == 525
    finally:
        set_cached_rates(None)


def test_media_model_pricing_hint_lists_all_video_tiers() -> None:
    """卡片资费含各清晰度；Mini 与 2.0 不同；2.5 含 1080p。"""
    from app.services.tokenfree_pricing import media_model_pricing_hint

    h20 = media_model_pricing_hint("seedance-2-0")
    h25 = media_model_pricing_hint("seedance-2-5")
    h_mm = media_model_pricing_hint("MiniMax-H3")
    assert "720p" in h20
    assert "1080p" in h25
    # 2.0 720p ≈ ¥1.52/秒（刊例 46×1.5×22k tok）
    assert "1.52" in h20 or "1.51" in h20 or "7.59" in h20
    assert "0.58" in h_mm or "0.584" in h_mm
    assert "768p" in h_mm or "480p" in h_mm


def test_charge_official_llm_uses_in_out_split() -> None:
    """LLM 预估按官方 in/out 70/30 拆。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    settings.billing_markup = 1.5
    settings.model_llm = "kimi-k2.6"
    rates = parse_pricing_payload(_sample_payload(), settings)
    set_cached_rates(rates)
    try:
        charge = charge_fen_official_llm(10_000, settings)
        assert charge > 1
        assert lookup_rate("kimi-k2.6") is not None
    finally:
        set_cached_rates(None)


def test_parse_pricing_item_skips_bad_row() -> None:
    """脏字段跳过，不让整包价目作废。"""
    from app.services.tokenfree_pricing import parse_pricing_item

    assert parse_pricing_item({"model_name": "x", "quota_type": "nope"}, usd_cny=7.0) is None


def test_video_catalog_id_from_endpoint() -> None:
    """方舟接入点名能收到 TokenFree 点分 Seedance id。"""
    assert video_catalog_id("doubao-seedance-2-5-260628") == "seedance-2.5"
    assert video_catalog_id("seedance-2-0-mini") == "seedance-2.0"  # mini 未开通→2.0
    assert video_catalog_id("doubao-seedance-2-0-260128") == "seedance-2.0"
    assert video_catalog_id("seedance-2") == "seedance-2.0"
    assert video_catalog_id("seedance-2.0-fast") == "seedance-2.0-fast"
    assert video_catalog_id("doubao-seedance-2-0-fast-260128") == "seedance-2.0-fast"


def test_canonicalize_channel_models_merges_seedance_2_aliases() -> None:
    """2.0 别名与 mini 合并到 2.0；fast / 2.5 保留。"""
    from app.services.tokenfree_pricing import canonicalize_channel_models

    assert canonicalize_channel_models(
        [
            "doubao-seedance-2-0-260128",
            "seedance-2",
            "seedance-2-0",
            "seedance-2-0-mini",
            "seedance-2.0-fast",
            "seedance-2-5",
        ]
    ) == ["seedance-2.0", "seedance-2.0-fast", "seedance-2.5"]


def test_canonicalize_keeps_unknown_seedance_id() -> None:
    """未知 Seedance 名不改写成 2.5。"""
    from app.services.tokenfree_pricing import canonicalize_channel_model_id

    assert canonicalize_channel_model_id("seedance-1.5") == "seedance-1.5"


def test_user_charge_applies_markup_and_rounds_up() -> None:
    """按成本扣费时乘加价倍率（1.06 即上浮 6%），向上取整到分；倍率低于 1 按 1 处理。"""
    from types import SimpleNamespace
    from app.services.billing.pricing import user_charge_fen

    assert user_charge_fen(100, SimpleNamespace(billing_markup=1.06)) == 106
    assert user_charge_fen(1, SimpleNamespace(billing_markup=1.06)) == 2
    assert user_charge_fen(50, SimpleNamespace(billing_markup=1.0)) == 50
    assert user_charge_fen(50, SimpleNamespace(billing_markup=0.5)) == 50
    assert user_charge_fen(0, SimpleNamespace(billing_markup=1.06)) == 0
