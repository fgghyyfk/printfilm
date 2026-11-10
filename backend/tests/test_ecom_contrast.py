"""精致版式字色：按对比度选深字 / 白字，中间调背景加雾面，次要文字也要看得清。"""

from PIL import Image, ImageStat

from app.services.ecom_set import layout, typo


def test_pick_ink_sides():
    """亮背景深字、暗背景白字；次要色对比度不低于下限。"""
    for bg, dark in (((240, 236, 230), True), ((40, 36, 32), False), ((158, 138, 119), True), ((124, 108, 95), False)):
        d, ink, muted = typo.pick_ink(bg)
        assert d is dark
        assert typo.contrast(muted, bg) >= typo.MUTED_MIN_CONTRAST - 0.05
        assert typo.contrast(ink, bg) >= typo.contrast(muted, bg)


def test_mid_tone_wall_subtitle_readable(tmp_path):
    """米驼色中间调墙面（线上实例：平均亮度 142）上副标题不再发虚。"""
    base = tmp_path / "base.jpg"
    Image.new("RGB", (900, 1200), (158, 138, 119)).save(base)
    out = tmp_path / "out.jpg"
    layout.render(base, out, size=(900, 1200), layout="title_corner", blank="top",
                  copy={"title": "随身香氛手护", "subtitle": "60g容量便携好携带", "en": "Portable"}, type_style="serif")
    img = Image.open(out).convert("RGB")
    # 雾面把文字区背景推亮，副标题行最深的像素与背景拉开
    bg = ImageStat.Stat(img.crop((600, 20, 880, 60))).mean
    assert typo.contrast(typo.INK, bg) >= typo.INK_MIN_CONTRAST - 0.3
    _, ink, muted = typo.pick_ink(bg)
    assert typo.contrast(muted, bg) >= typo.MUTED_MIN_CONTRAST - 0.05
