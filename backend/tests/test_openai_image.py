"""生图改走 OpenAI：尺寸换算、启用条件、edits/generations 请求与错误提示（HTTP 打桩，不发真实请求）。"""

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.services import openai_image as oi


def _settings(**kw):
    from app.config import get_settings

    return get_settings().model_copy(update={
        "openai_image_enabled": True, "openai_image_api_key": "sk-test", "openai_image_scope": "tools",
        "openai_image_model": "gpt-image-2", "openai_image_quality": "high", **kw,
    })


def _png_b64(color=(10, 120, 90)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# 尺寸：16 的倍数、总像素约 2.36MP、宽高比按画幅
@pytest.mark.parametrize("ratio,size,expect_r", [("3:4", None, 0.75), (None, "1728x2304", 0.75), ("1:1", None, 1.0), ("16:9", None, 16 / 9)])
def test_openai_size(ratio, size, expect_r):
    w, h = map(int, oi.openai_size(ratio, size).split("x"))
    assert w % 16 == 0 and h % 16 == 0
    assert 2_000_000 < w * h < 2_700_000
    assert abs(w / h - expect_r) < 0.03


# 极端画幅被夹到 3:1
def test_openai_size_clamps():
    w, h = map(int, oi.openai_size("10:1", None).split("x"))
    assert w / h <= 3.05


# 启用条件：开关 + Key + 作用范围
def test_active_conditions():
    assert oi.openai_image_active("tools", _settings())
    assert not oi.openai_image_active("all", _settings())                       # 默认只管工具中心
    assert oi.openai_image_active("all", _settings(openai_image_scope="all"))
    assert not oi.openai_image_active("tools", _settings(openai_image_enabled=False))
    assert not oi.openai_image_active("tools", _settings(openai_image_api_key=" "))


# 打桩 httpx：记录请求并返回假图
def _patch_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(oi.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


# 有参考图 → edits，多个 image[] 字段；结果落盘
async def test_generate_with_refs_uses_edits(tmp_path, monkeypatch):
    from app.services import storage

    ref = tmp_path / "scarf.png"
    Image.new("RGB", (40, 40), (200, 100, 150)).save(ref)
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: ref if url.startswith("/static/") else None)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}], "usage": {"total_tokens": 10}})

    _patch_http(monkeypatch, handler)
    out = await oi.generate("模特佩戴丝巾", ["/static/a.png", "/static/b.png"], project_id=0, shot_no=7,
                            size="1920x1920", ratio_hint="3:4", settings=_settings())
    assert seen["path"].endswith("/images/edits")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"].count(b'name="image[]"') == 2
    assert b"gpt-image-2" in seen["body"]
    assert out["local_path"].exists() and out["size"] == oi.openai_size("3:4", None)


# 无参考图 → generations（JSON）
async def test_generate_without_refs_uses_generations(tmp_path, monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}]})

    _patch_http(monkeypatch, handler)
    await oi.generate("一只杯子", None, project_id=0, shot_no=1, size=None, ratio_hint="1:1", settings=_settings())
    assert seen["path"].endswith("/images/generations")
    assert seen["json"]["model"] == "gpt-image-2" and seen["json"]["quality"] == "high"


# 错误转中文：Key 无效 / 额度用完
@pytest.mark.parametrize("status,payload,needle", [
    (401, {"error": {"message": "Incorrect API key"}}, "API Key"),
    (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}, "额度"),
])
async def test_generate_errors(tmp_path, monkeypatch, status, payload, needle):
    from app.services import storage

    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    _patch_http(monkeypatch, lambda request: httpx.Response(status, json=payload))
    with pytest.raises(RuntimeError, match=needle):
        await oi.generate("x", None, project_id=0, shot_no=1, size=None, settings=_settings())


# 后台设置：Key 字段按密钥处理（出参只给 has_ 标记）
def test_settings_secret_registered():
    from app.schemas_settings import SECRET_FIELD_FLAGS, model_config_field_names

    assert SECRET_FIELD_FLAGS["openai_image_api_key"] == "has_openai_image_api_key"
    names = model_config_field_names()
    for f in ("openai_image_enabled", "openai_image_api_key", "openai_image_model", "openai_image_quality", "openai_image_scope"):
        assert f in names


# gen_image：工具中心走 OpenAI 旁路，其它用途（默认 scope=tools）仍走 Seedream
async def test_gen_image_routes_by_purpose(tmp_path, monkeypatch):
    from app.services import ark as ark_mod

    gw = ark_mod.ArkGateway(settings=_settings(ark_mock=False))
    monkeypatch.setattr(ark_mod.ArkGateway, "mock", property(lambda self: False))
    called = {}

    async def fake_generate(prompt, refs, **kw):
        called["openai"] = (prompt, refs, kw.get("ratio_hint"))
        p = tmp_path / "x.png"
        p.write_bytes(base64.b64decode(_png_b64()))
        return {"local_path": p, "size": "1328x1776", "model": "gpt-image-2", "usage": {}}

    async def fake_seedream(self, *a, **kw):
        called["seedream"] = True
        return ark_mod.ImageResult(local_url="/static/s.png")

    monkeypatch.setattr(oi, "generate", fake_generate)
    monkeypatch.setattr(oi, "save_path_url", lambda p: "/static/generated/p0/x.png")
    monkeypatch.setattr(ark_mod.ArkGateway, "_seedream_once", fake_seedream)
    res = await gw.gen_image("模特", "文字", ["/static/a.png"], purpose="tools", ratio_hint="3:4")
    assert called["openai"][1] == ["/static/a.png"] and called["openai"][2] == "3:4"
    assert "避免：文字" in called["openai"][0]
    assert res.local_url == "/static/generated/p0/x.png" and res.raw_usage["provider"] == "openai"
    called.clear()
    await gw.gen_image("场景", "", None)
    assert called.get("seedream") and "openai" not in called
