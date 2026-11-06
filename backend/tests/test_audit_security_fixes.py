# -*- coding: utf-8 -*-
"""安全审计回归：路径穿越写文件、下载 SSRF、空密钥伪造回调、内置 Skill 越权。"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import billing as billing_api
from app.api.drama import skills as skills_api
from app.models import Order, User
from app.models_drama import DramaAsset
from app.schemas_agent import AgentSkillUpdateBody
from app.services import epay, openai_image, storage, studio_references
from app.services.drama import generation
from tests.conftest import make_user

PUBLIC_IP_URL = "http://93.184.216.34/a.png"
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)


def _mock_http(monkeypatch, handler) -> None:
    """让 httpx 客户端走 MockTransport，保留被测代码传入的其他参数。"""
    real_async, real_sync = httpx.AsyncClient, httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_async(transport=transport, **kw))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_sync(transport=transport, **kw))


def _settings(monkeypatch, **overrides) -> None:
    """覆盖 storage / 下载校验看到的站点与 OSS 配置。"""
    from app.config import get_settings
    from app.services import oss

    updated = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(oss, "get_settings", lambda: updated)
    monkeypatch.setattr(storage, "get_settings", lambda: updated)
    monkeypatch.setattr("app.services.url_guard.get_settings", lambda: updated, raising=False)


# ---------- 第 1 条：路径穿越 ----------


@pytest.mark.parametrize("url", [
    "/static/../../.env",
    "/static//etc/passwd",
    "/static/generated/../../../etc/cron.d/x",
    "https://evil.example/generated/../../../etc/cron.d/x",
    "https://evil.example/generated/p1/final.mp4",
])
def test_local_path_rejects_traversal_and_foreign_hosts(url):
    """越出 static 或外部域名都不能映射成本地路径。"""
    assert storage.local_path_from_url(url) is None


def test_local_path_keeps_site_and_oss_media(monkeypatch):
    """本站 /static、历史站点域名与配置的 OSS 地址继续映射到本地副本。"""
    _settings(monkeypatch, public_base_url="https://site.example", oss_enabled=True, oss_bucket="b",
              oss_access_key_id="k", oss_access_key_secret="s", oss_public_base="https://cdn.example",
              oss_folder="kepu")
    expected = storage.STATIC_ROOT / "generated/p1/a.png"
    assert storage.local_path_from_url("/static/generated/p1/a.png") == expected
    assert storage.local_path_from_url("https://site.example/static/generated/p1/a.png") == expected
    assert storage.local_path_from_url("https://www.printfilm.com/static/generated/p1/a.png") == expected
    assert storage.local_path_from_url("https://cdn.example/kepu/generated/p1/a.png?x=1") == expected


async def test_voice_source_url_cannot_write_outside_static(monkeypatch, tmp_path):
    """音色来源资产的恶意 URL 不能把响应写到 static 之外（原任意文件写入路径）。"""
    root = tmp_path / "app" / "static"
    (root / "generated").mkdir(parents=True)
    monkeypatch.setattr(storage, "STATIC_ROOT", root)
    target = tmp_path / "pwned.txt"
    calls = []

    def handler(request):
        """记录是否真的发出下载。"""
        calls.append(str(request.url))
        return httpx.Response(200, content=b"attacker bytes")

    _mock_http(monkeypatch, handler)
    source = DramaAsset(id=2, project_id=1, url="https://evil.example/generated/../../../pwned.txt", params={})
    character = DramaAsset(id=1, project_id=1, params={"voiceAudio": {"url": "x", "sourceAssetId": 2}})
    db = SimpleNamespace(get=AsyncMock(return_value=source), commit=AsyncMock(), refresh=AsyncMock())
    await generation.ensure_reference_assets_public_urls(db, [character], read_only=True)
    assert not target.exists()
    assert calls == []


# ---------- 第 2 条：SSRF ----------


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1:6379/",
    "http://10.0.0.8/a.png",
    "http://192.168.1.2/a.png",
    "http://100.100.100.200/latest",
    "http://[::1]/a.png",
    "http://[::ffff:127.0.0.1]/a.png",
])
async def test_download_rejects_internal_addresses(monkeypatch, tmp_path, url):
    """内网、回环、链路本地地址在发请求前就被拒绝。"""
    handler_calls = []
    _mock_http(monkeypatch, lambda request: handler_calls.append(request) or httpx.Response(200, content=b"x"))
    with pytest.raises(ValueError, match="内网"):
        await storage.download_to(url, tmp_path / "a.png")
    assert handler_calls == [] and not (tmp_path / "a.png").exists()


async def test_download_checks_every_redirect_hop(monkeypatch, tmp_path):
    """公网地址 302 到内网时拒绝，内网地址收不到请求。"""
    seen = []

    def handler(request):
        """首跳跳转到元数据地址。"""
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    _mock_http(monkeypatch, handler)
    with pytest.raises(ValueError, match="内网"):
        await storage.download_to(PUBLIC_IP_URL, tmp_path / "a.png")
    assert seen == ["93.184.216.34"]


async def test_download_rejects_domain_resolving_to_internal(monkeypatch, tmp_path):
    """域名解析到内网同样拒绝。"""
    from app.services import url_guard

    monkeypatch.setattr(url_guard, "_resolve_ips", AsyncMock(return_value=["10.1.2.3"]))
    _mock_http(monkeypatch, lambda request: httpx.Response(200, content=b"x"))
    with pytest.raises(ValueError, match="内网"):
        await storage.download_to("https://intranet.example/a.png", tmp_path / "a.png")


async def test_download_allows_public_and_configured_hosts(monkeypatch, tmp_path):
    """公网结果 URL、配置的本站与 OSS 域名、TokenFree 不受影响，跳转到公网也能跟随。"""
    from app.services import url_guard

    _settings(monkeypatch, public_base_url="http://localhost:8000", oss_public_base="https://oss.intra.example")
    resolver = AsyncMock(return_value=["10.1.2.3"])
    monkeypatch.setattr(url_guard, "_resolve_ips", resolver)

    def handler(request):
        """公网首跳跳到第二个公网地址，其余直接返回。"""
        if str(request.url) == PUBLIC_IP_URL:
            return httpx.Response(302, headers={"location": "http://93.184.216.35/b.png"})
        return httpx.Response(200, content=b"media")

    _mock_http(monkeypatch, handler)
    dest = tmp_path / "a.png"
    await storage.download_to(PUBLIC_IP_URL, dest)
    assert dest.read_bytes() == b"media"
    for url in ("http://localhost:8000/static/generated/p1/a.png",
                "https://oss.intra.example/kepu/a.png",
                "https://www.tokenfree.com/v1/videos/x/content"):
        await storage.download_to(url, dest)
    resolver.assert_not_awaited()


async def test_local_media_does_not_touch_network(monkeypatch, tmp_path):
    """本站 /static 与 mock 素材走本地文件，不经过下载校验。"""
    root = tmp_path / "static"
    (root / "mock").mkdir(parents=True)
    (root / "mock" / "a.svg").write_bytes(b"<svg/>")
    monkeypatch.setattr(storage, "STATIC_ROOT", root)
    monkeypatch.setattr(storage, "download_to", AsyncMock(side_effect=AssertionError("不应下载")))
    dest = tmp_path / "out.svg"
    assert await storage.ensure_local_media("/static/mock/a.svg", dest) == dest
    assert dest.read_bytes() == b"<svg/>"


async def test_materialize_asset_rejects_internal_url(monkeypatch):
    """图生图资产指向内网时返回中文 400 类错误，不发请求。"""
    calls = []
    _mock_http(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, content=PNG_1X1))
    monkeypatch.setattr(studio_references, "reference_params",
                        AsyncMock(return_value={"reference_images": ["http://169.254.169.254/a.png"]}))
    with pytest.raises(ValueError, match="内网"):
        await studio_references.materialize_image_assets(None, SimpleNamespace(id=1), [7])
    assert calls == []


async def test_materialize_hides_download_status_from_user(monkeypatch):
    """上游状态码统一成中文错误，不再用 500/400 差异当探测信号。"""
    _mock_http(monkeypatch, lambda request: httpx.Response(404))
    monkeypatch.setattr(studio_references, "reference_params",
                        AsyncMock(return_value={"reference_images": [PUBLIC_IP_URL]}))
    with pytest.raises(ValueError, match="下载失败"):
        await studio_references.materialize_image_assets(None, SimpleNamespace(id=1), [7])


async def test_openai_reference_rejects_internal_url(monkeypatch):
    """OpenAI 生图参考图同样校验。"""
    calls = []
    _mock_http(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, content=PNG_1X1))
    with pytest.raises(ValueError, match="内网"):
        await openai_image._ref_bytes("http://10.0.0.8/a.png")
    assert calls == []


def test_sync_history_fetch_rejects_internal_url(monkeypatch, tmp_path):
    """历史版本同步拉取遇到内网地址不发请求，回退原 URL。"""
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    calls = []
    _mock_http(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, content=b"x"))
    raw = "http://169.254.169.254/latest/a.png"
    assert generation._snapshot_version_media_url(raw, label="v1") == raw
    assert calls == []


# ---------- 第 3 条：空密钥伪造回调 ----------


def _forged_params(out_trade_no: str) -> dict[str, str]:
    """按空密钥自行计算签名的回调参数。"""
    params = {"out_trade_no": out_trade_no, "money": "1.00", "trade_status": "TRADE_SUCCESS"}
    raw = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return {**params, "sign": hashlib.md5(raw.encode()).hexdigest()}


def test_verify_rejects_when_key_empty(monkeypatch):
    """未配置密钥时任何签名都不通过。"""
    monkeypatch.setattr(epay, "get_settings", lambda: SimpleNamespace(epay_key=""))
    assert epay.verify(_forged_params("X")) is False
    monkeypatch.setattr(epay, "get_settings", lambda: SimpleNamespace(epay_key="  "))
    assert epay.verify(_forged_params("X")) is False


async def test_forged_notify_does_not_credit_when_key_empty(db_session, monkeypatch):
    """空密钥下伪造回调返回 fail，订单和余额不变。"""
    user = await make_user(db_session, balance_fen=0)
    order = Order(out_trade_no="AUDITFORGE1", user_id=user.id, sku_id="t", amount_fen=100, credit_fen=100,
                  pay_type="alipay", status="pending")
    db_session.add(order)
    await db_session.flush()
    monkeypatch.setattr(epay, "get_settings", lambda: SimpleNamespace(epay_key=""))

    class Request:
        """最小回调请求。"""
        method = "GET"
        query_params = _forged_params(order.out_trade_no)

    response = await billing_api.epay_notify(Request(), db=db_session)
    assert response.status_code == 400
    await db_session.refresh(order)
    await db_session.refresh(user)
    assert order.status == "pending" and int(user.balance_fen) == 0


async def test_create_order_not_persisted_when_payment_unconfigured(db_session, monkeypatch):
    """支付未配置时直接 503，不留下可被伪造入账的订单。"""
    user = await make_user(db_session)
    monkeypatch.setattr(epay, "get_settings", lambda: SimpleNamespace(epay_key="", epay_pid=""))
    body = billing_api.CreateOrderBody(sku_id=billing_api.billing.SKUS[0]["id"], pay_type="alipay")
    with pytest.raises(HTTPException) as exc:
        await billing_api.create_order(body, SimpleNamespace(headers={}, client=None), db=db_session, user=user)
    assert exc.value.status_code == 503
    count = await db_session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id))
    assert count == 0


# ---------- 第 7 条：内置 Skill ----------


async def test_regular_user_cannot_toggle_builtin_skill(monkeypatch):
    """普通用户停用内置 Skill 返回 403，全局开关不变。"""
    row = SimpleNamespace(is_builtin=True, user_id=None, is_active=True)
    update = AsyncMock()
    monkeypatch.setattr(skills_api, "get_visible_skill", AsyncMock(return_value=row))
    monkeypatch.setattr(skills_api, "update_user_skill", update)
    user = User(id=5, role="user")
    with pytest.raises(HTTPException) as exc:
        await skills_api.patch_skill(1, AgentSkillUpdateBody(is_active=False), db=None, user=user)
    assert exc.value.status_code == 403 and "内置" in exc.value.detail
    update.assert_not_awaited()
    assert row.is_active is True


async def test_admin_and_owner_can_still_patch_skill(monkeypatch):
    """管理员可开关内置 Skill，用户可改自己的 Skill。"""
    update = AsyncMock(side_effect=lambda db, row, **kw: row)
    monkeypatch.setattr(skills_api, "update_user_skill", update)
    monkeypatch.setattr(skills_api, "_out", lambda row: row)
    builtin = SimpleNamespace(is_builtin=True, user_id=None)
    own = SimpleNamespace(is_builtin=False, user_id=5)
    monkeypatch.setattr(skills_api, "get_visible_skill", AsyncMock(return_value=builtin))
    await skills_api.patch_skill(1, AgentSkillUpdateBody(is_active=False), db=None, user=User(id=9, role="admin"))
    monkeypatch.setattr(skills_api, "get_visible_skill", AsyncMock(return_value=own))
    await skills_api.patch_skill(2, AgentSkillUpdateBody(is_active=False), db=None, user=User(id=5, role="user"))
    assert update.await_count == 2
