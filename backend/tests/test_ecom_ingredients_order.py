"""成分图、默认组合顺序、调整顺序、英文花体、哑光融合。"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import categories, composite, ingredientscreen as ing, typo

KNOW = {"ingredients": [{"name": "透明质酸钠", "desc": "吸收并锁住水分，润养肌肤，长时滋润不干燥"},
                        {"name": "积雪草提取物", "desc": "植萃舒养，温柔呵护双手"},
                        {"name": "苦参根提取物", "desc": "亲和人体皮脂，守护娇嫩肌肤"}]}


def test_goods_second_image_is_not_white():
    """第二张不再是白底图；白底放第 5 张；10 张以上带成分图，12 张以上带痛点图。"""
    for n in categories.counts("goods"):
        combo = categories.composition("goods", n)
        assert combo[1] != "goods_white" and combo.index("goods_white") == 4
    assert "goods_ingredients_photo" in categories.composition("goods", 12)
    assert "goods_pain" in categories.composition("goods", 12)
    assert "goods_texture" in categories.composition("goods", 12)


def test_ingredient_prompt_and_cells(tmp_path):
    """四宫格提示词带每个成分、不出文字；切图得到 n 张正方形。"""
    assert ing.available(KNOW) and not ing.available({"ingredients": KNOW["ingredients"][:1]})
    p = ing.grid_prompt(ing.ingredients_of(KNOW))
    assert "第1格：透明质酸钠" in p and "第4格：干净的浅色留白" in p and "不要出现任何文字" in p
    grid = Image.new("RGB", (800, 800), "white")
    cells = ing.crop_cells(grid, 3)
    assert len(cells) == 3 and all(c.width == c.height for c in cells)


def test_ingredient_screen_render(tmp_path):
    """成分图排版：圆图 + 名称 + 说明 + 示意声明，文字可读。"""
    grid = tmp_path / "grid.jpg"
    im = Image.new("RGB", (800, 800), (240, 236, 230))
    d = ImageDraw.Draw(im)
    for i, c in enumerate([(120, 160, 90), (200, 200, 210), (150, 110, 80), (230, 230, 230)]):
        d.rectangle(((i % 2) * 400 + 20, (i // 2) * 400 + 20, (i % 2) * 400 + 380, (i // 2) * 400 + 380), fill=c)
    im.save(grid)
    out = ing.render(grid, tmp_path / "o.jpg", size=(900, 1200), copy={"title": "润泽舒养", "en": "Key Ingredients"},
                     styled={"knowledge": KNOW}, type_style="serif")
    img = Image.open(out)
    assert img.size == (900, 1200)
    # 三个圆图分别落在左、右、左
    px = np.asarray(img.convert("RGB")).astype(int)
    assert abs(px[int(1200 * 0.42), int(900 * 0.24)] - np.array([120, 160, 90])).sum() < 60


def test_script_en_label():
    """典雅风格英文小标用花体、保留大小写；其他风格全大写。"""
    serif, sans = typo.TYPE_STYLES["serif"], typo.TYPE_STYLES["sans"]
    assert serif.en_script and typo.en_label("PORTABLE HAND CREAM", serif) == "Portable Hand Cream"
    assert typo.en_label("Key Ingredients", serif) == "Key Ingredients"
    assert typo.en_label("Key Ingredients", sans) == "KEY INGREDIENTS"
    assert typo.SCRIPT_FONT.exists()
    assert "Pinyon" in typo.en_font(serif, 40).getname()[0]


def test_harmonize_flattens_ai_sheen():
    """AI 在商品上画的细条高光不再被搬到原图商品上（哑光包装不发滑）。"""
    cut = Image.new("RGBA", (120, 360), (200, 200, 200, 255))
    ai = Image.new("RGB", (120, 360), (200, 200, 200))
    ImageDraw.Draw(ai).rectangle((50, 0, 58, 359), fill=(255, 255, 255))  # 一条窄高光
    out = np.asarray(composite.harmonize(cut, ai).convert("RGB")).astype(float)
    stripe, rest = out[:, 50:58].mean(), out[:, 80:110].mean()
    assert stripe - rest < 20  # 改前约 +45


@pytest.mark.asyncio
async def test_reorder_and_ingredient_plan(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage
    from app.services.ecom_set import products, service as svc
    from app.services.ecom_set.ordering import reorder_items
    from tests.conftest import make_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db_session)
    png = tmp_path / "tube.png"
    tube = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
    ImageDraw.Draw(tube).rounded_rectangle((0, 0, 119, 359), 18, fill=(246, 244, 240, 255))
    tube.save(png)
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="", fabric=None,
                                        colors=[("No.04", png.read_bytes(), "a.png")])
    # 没有成分资料：成分图跳过并提示
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id,
                                 slots_picked=["goods_cover", "goods_ingredients_photo"])
    assert [it.slot for it in ecom.items] == ["goods_cover"]
    assert any("成分" in w for w in ecom.plan["warnings"])

    row.knowledge = KNOW
    await db_session.flush()
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id,
                                 slots_picked=["goods_cover", "goods_scene", "goods_ingredients_photo", "goods_white"])
    assert [it.slot for it in ecom.items] == ["goods_cover", "goods_scene", "goods_ingredients_photo", "goods_white"]
    assert ecom.plan["styled"]["knowledge"]["ingredients"]
    layout_before = dict(ecom.plan["styled"].get("layout") or {})

    with pytest.raises(ValueError):
        await reorder_items(db_session, user, ecom.id, [0, 1, 2])  # 不完整
    moved = await reorder_items(db_session, user, ecom.id, [0, 2, 1, 3])
    assert [it.slot for it in moved.items] == ["goods_cover", "goods_ingredients_photo", "goods_scene", "goods_white"]
    assert [it.idx for it in moved.items] == [0, 1, 2, 3]
    # 镜头 / 元素跟着图走
    if "1" in layout_before:
        assert moved.plan["styled"]["layout"]["2"] == layout_before["1"]

    for it in moved.items:
        it.status = "queued"
    await db_session.flush()
    with pytest.raises(ValueError):
        await reorder_items(db_session, user, ecom.id, [1, 0, 2, 3])
