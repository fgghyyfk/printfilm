"""成图文字可读性全量检查：各种背景 × 全部版式 × 全部字体风格，逐个字形核对字与正下方背景的对比度。

检查方式与出图代码的把关逻辑相互独立：拦截每一次往成图上画字，取字形实际覆盖的像素下的背景，
统计对比度不足 3:1 的像素占比。任何一行超过 4% 即失败。
"""

import random

import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import layout
from app.services.ecom_set.readable import contrast

SIZE = (540, 720)
COPY = {"title": "随身香氛手护霜", "subtitle": "60g容量便携好携带", "en": "Portable Hand Cream",
        "bullets": ["清润不粘", "植萃温和", "木质香调"]}
LAYOUTS = ("title_corner", "title_sub", "title_bullets", "label_bar")
STYLES = ("bold", "serif", "sans")
BAD_SHARE = 0.04
LOW = 3.0


def _backgrounds() -> dict[str, Image.Image]:
    """覆盖容易出问题的底图：各级中间调纯色、暖色墙、渐变、半明半暗、亮窗条纹、杂乱纹理。"""
    W, H = SIZE
    bgs: dict[str, Image.Image] = {}
    for v in range(0, 256, 17):
        bgs[f"gray{v}"] = Image.new("RGB", SIZE, (v, v, v))
    for name, c in {"beige": (158, 138, 119), "taupe": (124, 108, 95), "sand": (197, 187, 175),
                    "olive": (120, 128, 90), "rose": (190, 140, 140), "navy": (40, 52, 90)}.items():
        bgs[name] = Image.new("RGB", SIZE, c)
    for name, horiz in (("grad_v", False), ("grad_h", True)):
        im = Image.new("RGB", SIZE)
        d = ImageDraw.Draw(im)
        for i in range(W if horiz else H):
            v = int(30 + 210 * i / ((W if horiz else H) - 1))
            d.line([(i, 0), (i, H)] if horiz else [(0, i), (W, i)], fill=(v, v - 10, v - 20))
        bgs[name] = im
    split = Image.new("RGB", SIZE, (235, 230, 222))
    ImageDraw.Draw(split).rectangle((W // 2, 0, W, H), fill=(60, 52, 45))
    bgs["split"] = split
    window = Image.new("RGB", SIZE, (110, 96, 84))
    for x in range(0, W, 60):
        ImageDraw.Draw(window).rectangle((x, 0, x + 26, H), fill=(245, 240, 232))
    bgs["window_stripes"] = window
    rnd = random.Random(7)
    noisy = Image.new("RGB", SIZE)
    d = ImageDraw.Draw(noisy)
    for _ in range(900):
        x, y, s, v = rnd.randrange(W), rnd.randrange(H), rnd.randrange(8, 40), rnd.randrange(0, 256)
        d.rectangle((x, y, x + s, y + s), fill=(v, v, v))
    bgs["noisy"] = noisy
    return bgs


BACKGROUNDS = _backgrounds()


@pytest.fixture
def spy(monkeypatch):
    """拦截 ImageDraw.text：画字前取字形覆盖像素下的背景，记录对比度不足的像素占比。"""
    records: list[tuple[str, float]] = []
    orig = ImageDraw.ImageDraw.text

    def text(self, xy, txt, fill=None, font=None, *args, **kwargs):
        im = getattr(self, "_image", None)
        if im is not None and im.size == SIZE and im.mode == "RGBA" and txt.strip() and font is not None:
            mask = Image.new("L", SIZE, 0)
            ImageDraw.Draw(mask).text(xy, txt, fill=255, font=font)
            box = mask.getbbox()
            # 只核对画在不透明成图上的字（阴影层是透明图层）
            if box and im.getchannel("A").crop(box).getextrema()[0] == 255:
                region, m = im.convert("RGB").crop(box), mask.crop(box)
                col = tuple(fill[:3]) if isinstance(fill, tuple) else (0, 0, 0)
                cover = [p for p, a in zip(region.getdata(), m.getdata(), strict=True) if a > 128]
                if cover:
                    bad = sum(1 for p in cover if contrast(col, p) < LOW)
                    records.append((txt, bad / len(cover)))
        return orig(self, xy, txt, fill, font, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    return records


def _check(records, label):
    assert records, f"{label}: 没有画任何字"
    worst = max(records, key=lambda r: r[1])
    assert worst[1] <= BAD_SHARE, f"{label}: 「{worst[0]}」有 {worst[1]:.0%} 的笔画看不清"


@pytest.mark.parametrize("bg_name", sorted(BACKGROUNDS))
def test_all_layouts_readable(tmp_path, spy, bg_name):
    """每种背景上，全部版式 × 字体风格 × 留白位置的每一行字都看得清。"""
    base = tmp_path / "base.jpg"
    BACKGROUNDS[bg_name].save(base, quality=95)
    for lay in LAYOUTS:
        for style in STYLES:
            for blank in ("top", "bottom"):
                spy.clear()
                layout.render(base, tmp_path / "o.jpg", size=SIZE, layout=lay, blank=blank, copy=COPY,
                              type_style=style)
                _check(spy, f"{bg_name}/{lay}/{style}/{blank}")


@pytest.mark.parametrize("bg_name", sorted(BACKGROUNDS))
def test_vertical_title_readable(tmp_path, spy, bg_name):
    """丝巾封面竖排标题。"""
    base = tmp_path / "base.jpg"
    BACKGROUNDS[bg_name].save(base, quality=95)
    layout.render(base, tmp_path / "o.jpg", size=SIZE, layout="vertical_title", blank="none",
                  copy={"title": "春日花园", "subtitle": "真丝方巾 90cm"})
    _check(spy, f"{bg_name}/vertical")
