"""商品实拍图抠图：遮罩处理（收紧、去零碎、裁切）、颜色款抠图 / 切回原图、真实模型冒烟（有模型文件才跑）。"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import matting


def _mask(size=(400, 600), boxes=((120, 100, 280, 560),)) -> Image.Image:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for b in boxes:
        d.rectangle(b, fill=255)
    return m


def test_refine_and_keep_main():
    """背景残影归零；画面文字等小块去掉，两支并排的同款都保留。"""
    ghost = Image.fromarray(np.full((50, 50), 6, dtype=np.uint8), "L")
    assert np.asarray(matting.refine(ghost)).max() == 0
    m = _mask(boxes=((120, 100, 280, 560), (10, 10, 60, 30)))  # 主体 + 左上角一行字
    kept = np.asarray(matting.keep_main(m))
    assert kept[300, 200] == 255 and kept[20, 30] == 0
    pair = _mask(size=(600, 600), boxes=((60, 100, 250, 560), (350, 100, 540, 560)))
    kept2 = np.asarray(matting.keep_main(pair))
    assert kept2[300, 150] == 255 and kept2[300, 450] == 255


def test_apply_mask_crops_and_rejects_empty():
    """按主体裁切并留边；遮罩空或铺满整张时报中文错误。"""
    img = Image.new("RGB", (400, 600), (90, 120, 160))
    out = matting.apply_mask(img, _mask())
    assert out.mode == "RGBA" and out.width < 220 and out.height < 500
    assert out.getchannel("A").getextrema() == (0, 255)
    with pytest.raises(matting.MattingError, match="商品主体"):
        matting.apply_mask(img, Image.new("L", (400, 600), 0))
    with pytest.raises(matting.MattingError):
        matting.apply_mask(img, Image.new("L", (400, 600), 255))


def test_predict_mask_with_stub_session(monkeypatch):
    """预处理成 1024 输入、输出经 sigmoid 归一后缩回原图尺寸。"""
    seen = {}

    class Stub:
        def get_inputs(self):
            return [type("I", (), {"name": "input_image"})()]

        def run(self, _, feed):
            seen["shape"] = feed["input_image"].shape
            out = np.full((1, 1, 1024, 1024), -8.0, dtype=np.float32)
            out[0, 0, 200:800, 300:700] = 8.0
            return [out]

    monkeypatch.setattr(matting, "_get_session", lambda: Stub())
    m = matting.predict_mask(Image.new("RGB", (500, 800), "white"))
    assert seen["shape"] == (1, 3, 1024, 1024) and m.size == (500, 800)
    a = np.asarray(m)
    assert a[400, 250] > 250 and a[10, 10] < 5


@pytest.mark.skipif(not matting.available(), reason="本机没有抠图模型文件")
def test_real_model_cutout(tmp_path):
    """真实模型冒烟：浅色台面上的一支管子能抠出来，四角透明。"""
    img = Image.new("RGB", (700, 900), (214, 200, 184))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 640, 700, 900), fill=(232, 224, 214))
    d.rounded_rectangle((280, 180, 420, 700), 30, fill=(248, 248, 246), outline=(200, 200, 200), width=3)
    d.rectangle((290, 650, 410, 720), fill=(250, 250, 250))
    src = tmp_path / "photo.jpg"
    img.save(src)
    out = matting.cutout(src)
    a = np.asarray(out.getchannel("A"))
    assert a[0, 0] == 0 and a[-1, -1] == 0 and a[a.shape[0] // 2, a.shape[1] // 2] > 200


@pytest.mark.asyncio
async def test_cutout_color_and_restore(db_session, monkeypatch, tmp_path):
    from app.services import storage
    from app.services.ecom_set import products
    from tests.conftest import make_user

    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr(matting, "available", lambda: True)

    def fake_cutout_to(src, dest):
        cut = Image.new("RGBA", (50, 120), (0, 0, 0, 0))
        ImageDraw.Draw(cut).rectangle((5, 5, 45, 115), fill=(240, 240, 240, 255))
        cut.save(dest)
        return dest

    monkeypatch.setattr(matting, "cutout_to", fake_cutout_to)
    user = await make_user(db_session)
    photo = Image.new("RGB", (300, 400), (180, 170, 160))
    jpg = tmp_path / "photo.jpg"
    photo.save(jpg)
    png = tmp_path / "cut.png"
    fake_cutout_to(None, png)
    row = await products.create_product(db_session, user, name="护手霜", category="goods", size="60g", material="tube",
                                        notes="", fabric=None,
                                        colors=[("实拍", jpg.read_bytes(), "a.jpg"), ("透明", png.read_bytes(), "b.png")])
    out = products.to_out(row)
    assert [c["transparent"] for c in out["colors"]] == [False, True] and out["can_cutout"] is True

    row = await products.cutout_color(db_session, user, row.id, 0)
    c = products.to_out(row)["colors"][0]
    assert c["transparent"] is True and c["image_url"].endswith(".png") and c["photo_image_url"].endswith(".jpg")
    assert row.colors[0]["image_path"].endswith(".png") and Path(row.colors[0]["image_path"]).is_file()
    # 再抠一次用的仍是原图，原图记录不被覆盖
    row = await products.cutout_color(db_session, user, row.id, 0)
    assert row.colors[0]["photo_image_path"].endswith(".jpg")
    with pytest.raises(ValueError, match="透明底"):
        await products.cutout_color(db_session, user, row.id, 1)

    row = await products.restore_color_photo(db_session, user, row.id, 0)
    c = products.to_out(row)["colors"][0]
    assert c["transparent"] is False and c["image_url"].endswith(".jpg") and not c["photo_image_url"]
    with pytest.raises(ValueError, match="没有抠过"):
        await products.restore_color_photo(db_session, user, row.id, 0)
    with pytest.raises(LookupError):
        await products.cutout_color(db_session, user, row.id, 5)
