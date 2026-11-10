"""参考图内嵌：本地图压缩为 JPEG data URI，外部地址与 data URI 原样保留。"""

import base64
import io

from PIL import Image

from app.services import image_ref_inline, storage


# 大图与透明 PNG 都应缩到长边上限并转成白底 JPEG。
def test_encode_ref_image_downscales_and_flattens(tmp_path):
    src = tmp_path / "big.png"
    Image.new("RGBA", (4000, 2000), (0, 0, 0, 0)).save(src)
    uri = image_ref_inline.encode_ref_image(src)
    assert uri.startswith("data:image/jpeg;base64,")
    out = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert max(out.size) == image_ref_inline.INLINE_MAX_EDGE
    assert out.getpixel((10, 10)) == (255, 255, 255)


# 本站地址内嵌，外部 https 与已有 data URI 不动，本地文件缺失时保留原地址。
async def test_inline_local_refs_mixed(tmp_path, monkeypatch):
    local = tmp_path / "a.jpg"
    Image.new("RGB", (64, 64), (200, 10, 10)).save(local)
    mapping = {"http://localhost:8080/static/a.jpg": local, "/static/missing.jpg": tmp_path / "missing.jpg"}
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: mapping.get(url))
    out = await image_ref_inline.inline_local_refs([
        "http://localhost:8080/static/a.jpg", "https://cdn.example.com/x.png", "data:image/png;base64,AAAA", "/static/missing.jpg",
    ])
    assert out[0].startswith("data:image/jpeg;base64,")
    assert out[1:] == ["https://cdn.example.com/x.png", "data:image/png;base64,AAAA", "/static/missing.jpg"]
