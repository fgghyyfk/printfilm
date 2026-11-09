# -*- coding: utf-8 -*-
"""未配置模型渠道时：Mock 联调可走通，真实环境明确报错，电商图选项不崩。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.services import logical_model_router as router
from app.services.ecom_set import service as ecom_service


@pytest.fixture
def unbound_model(monkeypatch):
    """模拟模型已被托管但没有任何可用渠道绑定。"""
    snapshot = SimpleNamespace(logical_models=[], default_models={}, channels=[])
    monkeypatch.setattr(router, "get_routing_snapshot", lambda: snapshot)
    monkeypatch.setattr(router, "resolve_logical_model_id", lambda _cap, _model: "kimi-k2.6")
    monkeypatch.setattr(router, "has_managed_models", lambda *_a: True)
    monkeypatch.setattr(router, "is_model_enabled", lambda *_a: True)
    monkeypatch.setattr(router, "resolve_logical_model", lambda *_a: None)
    monkeypatch.setattr(router, "is_managed_model", lambda *_a: True)


def test_mock_mode_needs_no_channel(unbound_model, monkeypatch):
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    assert router.resolve_upstream_model("text", None) == "kimi-k2.6"


def test_real_mode_still_reports_missing_channel(unbound_model, monkeypatch):
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    with pytest.raises(ValueError, match="没有启用的可用渠道绑定"):
        router.resolve_upstream_model("text", None)


async def test_ecom_price_probe_survives_missing_channel(monkeypatch):
    """估价拿不到渠道时返回 0，不让选项和列表接口 500。"""
    monkeypatch.setattr(
        "app.services.media_models.planning.prepare_task_media",
        AsyncMock(side_effect=ValueError("model: x 没有启用的可用渠道绑定")),
    )
    assert await ecom_service.image_unit_fen(AsyncMock()) == 0
