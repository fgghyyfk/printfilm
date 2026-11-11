"""实拍图抠白底（浏览器内抠图）：上传时记透明底与否、取原图、保存抠图、切回原图。"""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _png(path: Path, transparent: bool) -> Path:
    im = Image.new("RGBA", (60, 120), (0, 0, 0, 0) if transparent else (200, 200, 200, 255))
    ImageDraw.Draw(im).rectangle((5, 5, 55, 115), fill=(240, 240, 240, 255))
    im.save(path)
    return path


@pytest.mark.asyncio
async def test_cutout_upload_and_restore(db_session, monkeypatch, tmp_path):
    from app.services import storage
    from app.services.ecom_set import products
    from tests.conftest import make_user

    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db_session)
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (300, 400), (180, 170, 160)).save(jpg)
    cut = _png(tmp_path / "cut.png", True)
    row = await products.create_product(db_session, user, name="护手霜", category="goods", size="60g", material="tube",
                                        notes="", fabric=None,
                                        colors=[("实拍", jpg.read_bytes(), "a.jpg"), ("透明", cut.read_bytes(), "b.png")])
    assert [c["transparent"] for c in products.to_out(row)["colors"]] == [False, True]
    original = products.color_photo_path(row, 0)
    assert original.suffix == ".jpg" and original.is_file()

    # 不是透明底 / 不是 PNG 的抠图结果拒收
    with pytest.raises(ValueError, match="透明底"):
        await products.set_color_cutout(db_session, user, row.id, 0, _png(tmp_path / "flat.png", False).read_bytes(), "x.png")
    with pytest.raises(ValueError, match="PNG"):
        await products.set_color_cutout(db_session, user, row.id, 0, jpg.read_bytes(), "x.jpg")

    row = await products.set_color_cutout(db_session, user, row.id, 0, cut.read_bytes(), "cutout.png")
    c = products.to_out(row)["colors"][0]
    assert c["transparent"] is True and c["image_url"].endswith(".png") and c["photo_image_url"].endswith(".jpg")
    # 再抠一次仍从原图抠，原图记录不被覆盖
    assert products.color_photo_path(row, 0) == original
    row = await products.set_color_cutout(db_session, user, row.id, 0, cut.read_bytes(), "cutout.png")
    assert row.colors[0]["photo_image_path"] == str(original)

    row = await products.restore_color_photo(db_session, user, row.id, 0)
    c = products.to_out(row)["colors"][0]
    assert c["transparent"] is False and c["image_url"].endswith(".jpg") and not c["photo_image_url"]
    with pytest.raises(ValueError, match="没有抠过"):
        await products.restore_color_photo(db_session, user, row.id, 0)
    with pytest.raises(LookupError):
        products.color_photo_path(row, 5)
