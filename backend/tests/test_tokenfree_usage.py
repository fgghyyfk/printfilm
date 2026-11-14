"""TokenFree / New API 结算日志与额度换算。"""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services.billing.display import resolve_billing_basis
from app.services.billing.pricing import parse_upstream_cost_fen
from app.services.tokenfree_gateway import tokenfree_site_origin
from app.services.tokenfree_usage import (
    fetch_tokenfree_consume_logs,
    quota_to_cost_fen,
)


def test_tokenfree_site_origin_strips_v1():
    assert tokenfree_site_origin("https://www.tokenfree.com/v1") == "https://www.tokenfree.com"
    assert tokenfree_site_origin("https://www.tokenfree.com/v1/") == "https://www.tokenfree.com"


def test_quota_to_cost_fen_one_usd():
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    assert quota_to_cost_fen(500_000, settings) == 700
    assert quota_to_cost_fen(0, settings) == 0


def test_parse_upstream_cost_fen_from_newapi_quota():
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    assert parse_upstream_cost_fen({"usage": {"quota_consumed": 500_000}}, settings) == 700
    assert parse_upstream_cost_fen(
        {"usage": {"prompt_tokens": 1, "quota": 500_000}},
        settings,
    ) == 700
    assert parse_upstream_cost_fen({"prompt_tokens": 1, "quota": 500_000}, settings) == 700
    assert parse_upstream_cost_fen({"quota": 500_000}, settings) is None


def test_pick_migratable_api_key_skips_moonshot_and_ark():
    """启动迁 Key 时不要把 Moonshot/方舟 Key 写进 TokenFree。"""
    from app.schemas_routing import SystemModelChannel
    from app.services.tokenfree_gateway import pick_migratable_api_key

    moonshot = SystemModelChannel(
        id="openai-default",
        name="Moonshot",
        base_url="https://api.moonshot.cn/v1",
        api_key="sk-moonshot",
    )
    ark = SystemModelChannel(
        id="ark-default",
        name="Ark",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key="ark-key",
    )
    assert pick_migratable_api_key([moonshot, ark]) == ""
    tokenfree = SystemModelChannel(
        id="tokenfree",
        name="TokenFree",
        base_url="https://www.tokenfree.com/v1",
        api_key="sk-tokenfree",
    )
    assert pick_migratable_api_key([moonshot, tokenfree]) == "sk-tokenfree"
    aliased = SystemModelChannel(
        id="legacy-openai",
        name="TF",
        base_url="https://www.tokenfree.com/v1",
        api_key="sk-from-url",
    )
    assert pick_migratable_api_key([moonshot, aliased]) == "sk-from-url"


def test_parse_upstream_cost_fen_prefers_quota_over_usd_cost():
    """New API 的 cost 是美元；与 quota 同时出现时按 quota，避免 0.625 被当成 63 分。"""
    settings = get_settings()
    settings.billing_usd_cny = 7.0
    # 0.625 USD × 500000 quota/USD = 312500 quota → ¥4.375 → 438 分
    assert parse_upstream_cost_fen(
        {"usage": {"prompt_tokens": 1, "quota": 312_500, "cost": 0.625}},
        settings,
    ) == 438
    # 无 quota 字段时仍把火山 cost 当人民币元
    assert parse_upstream_cost_fen({"usage": {"cost": 1.23}}, settings) == 123


def test_resolve_billing_basis_newapi_quota():
    raw = '{"usage": {"prompt_tokens": 10, "quota": 500000}}'
    assert resolve_billing_basis(estimated=False, raw_usage_json=raw) == "upstream_cost"


@pytest.mark.asyncio
@pytest.mark.parametrize("zero_based", [False, True])
async def test_consume_logs_keep_billing_requests_and_pagination(monkeypatch, zero_based):
    """结算仍能按时间和模型检索真实消费日志，并兼容从第0页起的站点。"""
    rows = [{"quota": 500_000, "model_name": "test-model"}]
    pages = []

    def handle(request):
        assert request.url.path == "/api/log/self"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.params["type"] == "2"
        assert request.url.params["start_timestamp"] == "100"
        assert request.url.params["end_timestamp"] == "200"
        assert request.url.params["model_name"] == "test-model"
        page = request.url.params["p"]
        pages.append(page)
        items = [] if zero_based and page == "1" else rows
        return httpx.Response(200, json={"success": True, "data": {"total": 1, "items": items}})

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr("app.services.tokenfree_usage.resolve_tokenfree_api_key", lambda: "test-key")
    monkeypatch.setattr("app.services.tokenfree_usage.tokenfree_site_origin", lambda: "https://example.test")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))

    assert await fetch_tokenfree_consume_logs(100, 200, model_name=" test-model ") == rows
    assert pages == (["1", "0"] if zero_based else ["1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(401, "unauthorized"), (200, "not-json"), (200, '{"success": false}')])
async def test_consume_logs_unavailable_does_not_interrupt_billing(monkeypatch, status, body):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    monkeypatch.setattr("app.services.tokenfree_usage.resolve_tokenfree_api_key", lambda: "test-key")
    monkeypatch.setattr("app.services.tokenfree_usage.tokenfree_site_origin", lambda: "https://example.test")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))

    assert await fetch_tokenfree_consume_logs(100, 200) == []
