"""Ark 原生生图：本地 /static 参考图转 data URI，https 原样保留（纯单测）。"""

from app.services.ark import ArkGateway


# _resolve_image_ref 对本地文件返回 data URI，对 https 原样返回
async def test_resolve_local_and_https(tmp_path, monkeypatch):
    from app.services import storage

    f = tmp_path / "ref.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: f if url.startswith("/static/") else None)
    gw = ArkGateway()
    assert (await gw._resolve_image_ref("/static/generated/x/ref.png")).startswith("data:image/")
    assert await gw._resolve_image_ref("https://cdn.example.com/a.png") == "https://cdn.example.com/a.png"


# 有 https 公网站点时，本地参考图转站点公网地址；否则退回 data URI
async def test_native_ref_prefers_public_https(tmp_path, monkeypatch):
    from app.services import storage

    f = tmp_path / "ref.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: f if url.startswith("/static/") else None)
    gw = ArkGateway()
    monkeypatch.setattr(gw.settings, "public_base_url", "https://kaikou.example.com")
    assert await gw._native_image_ref("/static/generated/x/ref.png") == "https://kaikou.example.com/static/generated/x/ref.png"
    monkeypatch.setattr(gw.settings, "public_base_url", "http://localhost:8000")
    assert (await gw._native_image_ref("/static/generated/x/ref.png")).startswith("data:image/")
