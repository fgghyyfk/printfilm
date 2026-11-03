"""生成图自动校色：商品颜色向参考色板收敛，灰背景与肤色不动；画面里没有商品颜色时不修改。"""

from pathlib import Path

import numpy as np
from PIL import Image

from app.services import color_match as cm

GREEN, PINK, PURPLE = (40, 160, 125), (240, 190, 215), (170, 140, 210)
GREY, SKIN = (200, 200, 205), (225, 185, 160)


# 参考图：三块商品色
def _reference() -> np.ndarray:
    ref = np.zeros((90, 90, 3), dtype=float)
    ref[:30], ref[30:60], ref[60:] = GREEN, PINK, PURPLE
    return ref


# 生成图：商品色整体偏暖（a*/b* +6）、偏淡，外加灰背景与肤色块
def _generated() -> np.ndarray:
    lab = cm.to_lab(_reference())
    lab[..., 1] = lab[..., 1] * 0.85 + 6
    lab[..., 2] = lab[..., 2] * 0.85 + 6
    product = cm.to_rgb(lab)
    gen = np.zeros((90, 180, 3), dtype=float)
    gen[:, :90] = product
    gen[:, 90:135] = GREY
    gen[:, 135:] = SKIN
    return gen


def _mean_ab(rgb: np.ndarray) -> tuple[float, float]:
    lab = cm.to_lab(rgb)
    return float(lab[..., 1].mean()), float(lab[..., 2].mean())


# Lab 往返误差很小
def test_lab_roundtrip():
    rgb = np.array([[GREEN, PINK, GREY, SKIN]], dtype=float)
    assert np.abs(cm.to_rgb(cm.to_lab(rgb)) - rgb).max() < 1.0


# 商品颜色向参考收敛；灰背景、肤色基本不变
def test_match_pulls_product_and_keeps_neutrals():
    ref, gen = _reference(), _generated()
    out, stats = cm.match_colors_array(gen, ref)
    assert stats["applied"] and stats["protect_skin"]
    ra, rb = _mean_ab(ref)
    ga, gb = _mean_ab(gen[:, :90])
    oa, ob = _mean_ab(out[:, :90])
    assert abs(oa - ra) < abs(ga - ra) and abs(ob - rb) < abs(gb - rb)
    assert np.abs(out[:, 90:135] - gen[:, 90:135]).max() < 2.0   # 灰背景
    assert np.abs(out[:, 135:] - gen[:, 135:]).max() < 2.0       # 肤色


# 画面里只有灰与肤色（商品不在画面）时不修改
def test_no_product_colors_skips():
    gen = np.zeros((60, 60, 3), dtype=float)
    gen[:, :30], gen[:, 30:] = GREY, SKIN
    out, stats = cm.match_colors_array(gen, _reference())
    assert stats["applied"] is False
    assert np.array_equal(out, gen)


# 文件版：校色时写出 PNG，未校色不写
def test_match_file(tmp_path: Path):
    ref_p, gen_p, out_p = tmp_path / "ref.png", tmp_path / "gen.png", tmp_path / "out.png"
    Image.fromarray(_reference().astype(np.uint8)).save(ref_p)
    Image.fromarray(_generated().astype(np.uint8)).save(gen_p)
    assert cm.match_colors_file(gen_p, ref_p, out_p)["applied"] and out_p.exists()
    grey_p, out2 = tmp_path / "grey.png", tmp_path / "out2.png"
    Image.new("RGB", (40, 40), GREY).save(grey_p)
    assert not cm.match_colors_file(grey_p, ref_p, out2)["applied"] and not out2.exists()


# 色板序号映射到参考文件
def test_color_ref_file_mapping():
    from app.services.studio_generation import _color_ref_file

    files = [Path("a.png"), Path("b.png")]
    assert _color_ref_file({"color_match_ref": 1}, files) == Path("b.png")
    assert _color_ref_file({"color_match_ref": 5}, files) is None
    assert _color_ref_file({}, files) is None


# .jpg 输出存为 JPEG（体积小，线上加载快）
def test_match_file_jpeg_output(tmp_path: Path):
    ref_p, gen_p, out_p = tmp_path / "ref.png", tmp_path / "gen.png", tmp_path / "out.jpg"
    Image.fromarray(_reference().astype(np.uint8)).save(ref_p)
    Image.fromarray(_generated().astype(np.uint8)).save(gen_p)
    assert cm.match_colors_file(gen_p, ref_p, out_p)["applied"]
    with Image.open(out_p) as im:
        assert im.format == "JPEG"
