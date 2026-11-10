"""电商图商品实际尺寸与「不加文字」：尺寸规整、比例提示词、尺寸标注屏、不加文字排版、建套图整合。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import categories, dims, infoscreens
from app.services.ecom_set.layout import render


def _tube() -> Image.Image:
    im = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 119, 359), 18, fill=(246, 244, 240, 255))
    return im


# 尺寸规整、文字、参照物
def test_dims_helpers():
    assert dims.normalize({"h": "15", "w": "4.25cm", "d": "", "x": 9}) == {"h": 15.0, "w": 4.2}
    assert dims.normalize({"h": -1, "w": 999}) == {}
    assert dims.parse_form("15", "", "3") == {"h": 15.0, "d": 3.0}
    assert dims.text({"h": 15, "w": 4}) == "高 15 厘米 × 宽 4 厘米"
    assert dims.short({"h": 15, "w": 4, "d": 3}) == "15×4×3cm"
    assert "手机" in dims.reference({"h": 15})
    assert "倍" in dims.reference({"h": 60}) or "椅子" in dims.reference({"h": 60}) or "小臂" in dims.reference({"h": 60})
    assert dims.scale_note({}) == "" and dims.plan_note(None) == ""
    assert dims.scale_note({"h": 15}, ref="texture") == ""
    assert "手掌" in dims.scale_note({"h": 15}, ref="hand")
    assert "道具" in dims.scale_note({"h": 15}, ref="composite")


# 出图提示词：标品各槽位、配饰模特槽位带比例约束；丝巾不带
def test_prompt_scale():
    d = {"h": 15, "w": 4}
    goods = categories.build_prompt("goods", slot_prompt="x", scene="y", ref="composite", size=None,
                                    material=None, has_fabric=False, dims=d)
    assert "商品真实尺寸：高 15 厘米" in goods and "道具" in goods
    hand = categories.build_prompt("goods", slot_prompt="x", scene="y", ref="hand", size=None,
                                   material=None, has_fabric=False, dims=d)
    assert "手掌" in hand
    bag = categories.build_prompt("bag", slot_prompt="x", scene="y", ref="model", size=None,
                                  material=None, has_fabric=False, dims={"h": 22, "w": 30, "d": 12})
    assert "模特" in bag and "高 22 厘米" in bag
    scarf = categories.build_prompt("scarf", slot_prompt="x", scene="y", ref="model", size="88",
                                    material="silk", has_fabric=False, dims=d)
    assert "商品真实尺寸" not in scarf
    assert "商品真实尺寸" not in categories.build_prompt("goods", slot_prompt="x", scene="y", ref="composite",
                                                       size=None, material=None, has_fabric=False)


# 不加文字：排版直接输出底图；尺寸标注屏有尺寸才可用，并画出标注
def test_no_text_and_size_screen(tmp_path):
    base = tmp_path / "base.jpg"
    Image.new("RGB", (790, 1053), (230, 225, 218)).save(base)
    copy = {"title": "温柔护手 一抹润泽", "subtitle": "乳木果 · 小苍兰", "bullets": ["不黏腻", "持久留香"]}
    plain = render(base, tmp_path / "a.jpg", size=(790, 1053), layout="title_bullets", blank="top", copy=copy,
                   type_style="none")
    texted = render(base, tmp_path / "b.jpg", size=(790, 1053), layout="title_bullets", blank="top", copy=copy,
                    type_style="serif")
    a = np.asarray(Image.open(plain).convert("L")).astype(int)
    b = np.asarray(Image.open(texted).convert("L")).astype(int)
    assert a.std() < 3 and np.abs(a - b).max() > 60

    assert not infoscreens.available("goods_info_size", {}, [], None)
    assert not infoscreens.available("goods_info_size", {}, [], {"d": 3})
    assert infoscreens.available("goods_info_size", {}, [], {"h": 15})
    cut = tmp_path / "cut.png"
    _tube().save(cut)
    out = infoscreens.render_info("goods_info_size", tmp_path / "size.jpg", size=(790, 1053), copy={},
                                  styled={"dims": {"h": 15, "w": 4, "d": 3}, "product_path": str(cut)},
                                  type_style="serif")
    g = np.asarray(Image.open(out).convert("L"))
    assert (g < 120).sum() > 500
    rows = infoscreens.spec_rows({"dims": {"h": 15, "w": 4}, "product_name": "护手霜"})
    assert ("尺寸", "15×4cm") in rows


# 整合：确认页填尺寸 + 不加文字 → 记回商品库、进提示词、AI 槽位不留文字区、可切回加文字
async def test_dims_no_text_flow(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage
    from app.services.ecom_set import jobs, products, service as svc
    from tests.conftest import make_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db_session)
    png = tmp_path / "tube.png"
    _tube().save(png)
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="", fabric=None,
                                        colors=[("No.04", png.read_bytes(), "a.png")])
    assert products.to_out(row)["dims"] == {}
    # 没填尺寸时尺寸标注被跳过并提示
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id,
                                 slots_picked=["goods_cover", "goods_scene", "goods_info_size"])
    assert "goods_info_size" not in [it.slot for it in ecom.items]
    assert any("实际尺寸" in w for w in ecom.plan["warnings"])

    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id,
                                 slots_picked=["goods_cover", "goods_scene", "goods_info_size"],
                                 dims={"h": 15, "w": 4}, no_text=True)
    await db_session.refresh(row)
    assert row.dims == {"h": 15.0, "w": 4.0}
    o = svc.to_out(ecom, image_fen=95)["styled"]
    assert o["dims"] == {"h": 15.0, "w": 4.0} and o["no_text"]
    items = {it.slot: it for it in ecom.items}
    assert items["goods_info_size"].copy_json["title"] == "产品尺寸"
    assert "goods_info_size" in items and items["goods_cover"].blank == "none" and items["goods_scene"].blank == "none"
    assert "商品实际尺寸：高 15 厘米" in ecom.notes
    assert jobs.type_style_of(ecom) == "none"

    # 下次不填尺寸：沿用商品库里的
    again = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                  notes="", category="goods", frame="taobao_34", product_id=row.id,
                                  slots_picked=["goods_cover"])
    assert svc.to_out(again, image_fen=95)["styled"]["dims"] == {"h": 15.0, "w": 4.0}
    assert again.items[0].blank != "none" and not svc.to_out(again, image_fen=95)["styled"]["no_text"]

    started = await svc.start_generation(db_session, user, ecom.id)
    assert any(it.status == "succeeded" for it in started.items)
    switched = await svc.set_type_style(db_session, user, ecom.id, "serif")
    out = svc.to_out(switched, image_fen=95)["styled"]
    assert not out["no_text"] and out["type_style"] == "serif"
    back = await svc.set_type_style(db_session, user, ecom.id, "none")
    assert svc.to_out(back, image_fen=95)["styled"]["no_text"]
    with pytest.raises(ValueError):
        await svc.set_type_style(db_session, user, ecom.id, "comic")


def test_hand_note_gives_concrete_ratio():
    """手持比例：给出相对手长、手指宽的具体倍数，并要求手腕入镜。"""
    note = dims.hand_note({"h": 14.7, "w": 4.6})
    assert "整只手长度的 0.9 倍" in note
    assert "3 根手指并拢" in note and "6 成" in note
    assert "手腕" in note
    assert note in dims.scale_note({"h": 14.7, "w": 4.6}, ref="hand")


def test_hand_note_grip_by_size():
    """很小的商品用两指捏，很大的商品托着；宽度超过手掌时不按手指数。"""
    small = dims.hand_note({"h": 4, "w": 1.5})
    assert "拇指和食指捏住" in small and "约一根手指宽" in small and "绕过" not in small
    big = dims.hand_note({"h": 40, "w": 25})
    assert "托着" in big and "手掌宽度的 3.3 倍" in big and "根手指" not in big.split("；", 1)[1]
    assert dims.hand_note({}) == ""


def test_reference_always_has_times():
    """参照物比例小于 1 时也写「倍」。"""
    assert dims.reference({"h": 4}).endswith("倍")
