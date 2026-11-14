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


# 本地参考图压成小 JPEG data URI（长边 ≤1024），https 原样保留
async def test_native_ref_compacts_local(tmp_path, monkeypatch):
    import base64
    import io

    from PIL import Image

    from app.services import storage

    f = tmp_path / "ref.png"
    Image.new("RGBA", (3000, 2000), (20, 160, 120, 255)).save(f)
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: f if url.startswith("/static/") else None)
    gw = ArkGateway()
    out = await gw._native_image_ref("/static/generated/x/ref.png")
    assert out.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(out.split(",", 1)[1]))) as im:
        assert max(im.size) == 1024
        r, g, b = im.getpixel((10, 10))
        assert abs(r - 20) < 6 and abs(g - 160) < 6 and abs(b - 120) < 6
    assert await gw._native_image_ref("https://cdn.example.com/a.png") == "https://cdn.example.com/a.png"
