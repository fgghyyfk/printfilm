"""生图渠道 Promptoll：请求形态（要 b64_json、image[] 参考图）、回 URL 时下载、成本头、报错文案；电商套图出图模型选择。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.services import openai_image


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _settings(monkeypatch, **kw):
    from app.config import get_settings

    s = get_settings()
    values = {"openai_image_enabled": True, "openai_image_provider": "promptoll", "promptoll_api_key": "zai_test",
              "openai_image_api_key": "", "openai_image_quality": "medium", "openai_image_model": "gpt-image-2", **kw}
    for k, v in values.items():
        monkeypatch.setattr(s, k, v)
    return s


_REAL_CLIENT = httpx.AsyncClient


def _mock_client(monkeypatch, handler):
    real = _REAL_CLIENT

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.pop("event_hooks", None)
        return real(*args, **kwargs)

    monkeypatch.setattr(openai_image.httpx, "AsyncClient", factory)


# 渠道解析：选 Promptoll 用固定地址和独立 Key；没填 Key 视为未开通
def test_endpoint_and_configured(monkeypatch):
    s = _settings(monkeypatch)
    assert openai_image.image_endpoint(s) == ("promptoll", "https://api.promptoll.com/v1", "zai_test")
    assert openai_image.openai_image_configured(s) and openai_image.provider_label(s) == "Promptoll"
    monkeypatch.setattr(s, "promptoll_api_key", "")
    monkeypatch.setattr(s, "openai_image_api_key", "sk-x")
    assert not openai_image.openai_image_configured(s)
    monkeypatch.setattr(s, "openai_image_provider", "openai")
    assert openai_image.openai_image_configured(s) and openai_image.image_endpoint(s)[0] == "openai"


# 有参考图走 edits：带 image[] 与 response_format=b64_json，读 X-Price-Microcents 成本
async def test_promptoll_edits(monkeypatch, tmp_path):
    from app.services import storage

    s = _settings(monkeypatch)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png())
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: ref)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png()).decode()}],
                                         "usage": {"total_tokens": 1200}},
                              headers={"X-Price-Microcents": "17500000"})

    _mock_client(monkeypatch, handler)
    out = await openai_image.generate("画一支护手霜", ["/static/x.png"], project_id=0, shot_no=1, size="1328x1776",
                                      ratio_hint="3:4", settings=s)
    assert seen["url"] == "https://api.promptoll.com/v1/images/edits" and seen["auth"] == "Bearer zai_test"
    assert b'name="response_format"' in seen["body"] and b"b64_json" in seen["body"] and b'name="image[]"' in seen["body"]
    assert out["provider"] == "promptoll" and out["usage"]["cost_usd"] == pytest.approx(0.175)
    assert Path(out["local_path"]).exists() and out["size"] == "1328x1776"


# 回 URL 时自动下载
async def test_promptoll_url_fallback(monkeypatch, tmp_path):
    from app.services import storage

    s = _settings(monkeypatch)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/a.png"}]})
        return httpx.Response(200, content=_png())

    _mock_client(monkeypatch, handler)
    out = await openai_image.generate("画", [], project_id=0, shot_no=1, size="1024x1024", settings=s)
    assert Path(out["local_path"]).read_bytes() == _png() and "cost_usd" not in out["usage"]


# 文生图：普通 Key 不支持高画质时去掉 quality 自动重试一次
async def test_promptoll_hd_retry(monkeypatch, tmp_path):
    import json as _json

    from app.services import storage

    s = _settings(monkeypatch, openai_image_quality="high")
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = _json.loads(req.content)
        bodies.append(body)
        if "quality" in body:
            return httpx.Response(400, json={"detail": "model gpt-image-2-hd not found"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]})

    _mock_client(monkeypatch, handler)
    out = await openai_image.generate("画", [], project_id=0, shot_no=1, size="1024x1024", settings=s)
    assert Path(out["local_path"]).exists() and len(bodies) == 2 and "quality" not in bodies[1]


# 报错转中文：高画质未开通、余额不足、Key 无效
async def test_promptoll_errors(monkeypatch, tmp_path):
    s = _settings(monkeypatch, openai_image_quality="high")
    cases = [(400, {"detail": "model gpt-image-2-hd unavailable"}, "高画质"), (402, {"detail": "insufficient"}, "余额不足"),
             (401, {"error": {"message": "bad key"}}, "Key 无效")]
    for status, body, expect in cases:
        _mock_client(monkeypatch, lambda req, st=status, b=body: httpx.Response(st, json=b))
        with pytest.raises(RuntimeError, match=expect):
            await openai_image.generate("画", [], project_id=0, shot_no=1, size="1024x1024", settings=s)


# 电商套图：出图模型写进任务计价参数；没开通时选 GPT 报错
def test_ecom_engine_payload(monkeypatch):
    from app.services.ecom_set import service

    assert service.image_task_payload("3:4") == {"aspect_ratio": "3:4", "resolution": "2K"}
    assert service.image_task_payload("3:4", "gpt-image-2")["model_id"] == "gpt-image-2"
    assert service.normalize_engine("dall-e") == "seedream"
    _settings(monkeypatch, promptoll_api_key="")
    assert [e["id"] for e in service.engine_options()] == ["seedream"]
    from app.services.ecom_set.styled import _pick_engine

    with pytest.raises(ValueError, match="未开通"):
        _pick_engine("gpt-image-2")
    _settings(monkeypatch)
    assert _pick_engine("gpt-image-2") == "gpt-image-2"
    assert [e["id"] for e in service.engine_options()] == ["seedream", "gpt-image-2"]
    assert "Promptoll" in service.engine_options()[1]["name"]
