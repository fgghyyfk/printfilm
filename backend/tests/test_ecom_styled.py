"""电商图「模特上身」：品类注册表、勾选槽位、画幅、面料参考、商品库、同款换色、长图、生成模特（ARK_MOCK / 打桩）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.models_drama import DramaAsset, DramaProject
from app.services.ecom_set import categories, presets
from tests.conftest import make_user


def _png(path: Path, color=(200, 120, 140)) -> Path:
    img = Image.new("RGB", (400, 400), color)
    for x in range(0, 400, 40):
        img.paste((40, 120, 160), (x, x, x + 20, x + 20))
    img.save(path)
    return path


# 注册表：各品类都有材质、张数构成与槽位，槽位 key 都已登记
def test_categories_options():
    opts = {c["id"]: c for c in categories.options()}
    assert set(opts) == {"scarf", "apparel", "jewelry", "bag", "goods"}
    assert opts["goods"]["uses_model"] is False and opts["goods"]["material_label"] == "包装形态"
    for c in opts.values():
        assert c["materials"] and c["counts"] and c["slots"]
        for keys in c["compositions"].values():
            assert all(k in presets.SLOTS for k in keys)
    assert opts["scarf"]["sizes"] and not opts["bag"]["sizes"]


# 通用品类提示词：模特 + 商品双参考、材质质感；有面料图时追加说明
def test_generic_prompt():
    p = categories.build_prompt("bag", slot_prompt="肩背", scene="", ref="model", size="", material="cowhide", has_fabric=True)
    assert p.startswith("参考图1是固定模特") and "头层牛皮" in p and p.endswith(categories.FABRIC_HINT)
    q = categories.build_prompt("jewelry", slot_prompt="主图", scene="", ref="product", size="", material="pearl", has_fabric=False)
    assert "固定模特" not in q and "珠光" in q


# 勾选槽位：只保留该品类的合法槽位；全不合法时报错
def test_slots_picked():
    keys = [s.key for s in presets.slots_for("scarf", 6, ["scarf_knot", "bag_hero", "scarf_knot", "scarf_size"])]
    assert keys == ["scarf_knot", "scarf_size"]
    with pytest.raises(ValueError):
        presets.slots_for("scarf", 6, ["bag_hero"])


# 画幅：主图 1:1 生成比例为 1:1，输出 1440²；未知画幅回落淘宝 3:4
def test_frames():
    assert presets.output_size("main_11", "apparel_front") == (1440, 1440)
    assert presets.gen_ratio("main_11", "apparel_front") == "1:1"
    assert presets.output_size("taobao", "scarf_cover") == (1440, 1920)


# 长图：按顺序竖向拼接，宽 1080
def test_long_image(tmp_path):
    import io

    from app.services.ecom_set.styled import build_long_image

    a, b = _png(tmp_path / "a.png"), _png(tmp_path / "b.png", (10, 200, 10))
    with Image.open(io.BytesIO(build_long_image([a, b]))) as im:
        assert im.size == (1080, 2160)


async def _setup(db, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage, tool_models

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title="模特", params={})
    db.add(project)
    await db.flush()
    model = DramaAsset(project_id=project.id, type="image", asset_type="image", name="模特A",
                       url="/static/generated/model_a.png", params={})
    db.add(model)
    await db.flush()
    await tool_models.update_model(db, user.id, model.id, is_model_flag=True, is_default=True)
    return user, model


# 商品库 → 规划（勾选槽位、主图画幅、面料图）→ 同款换色
async def test_product_plan_and_recolor(db_session, monkeypatch, tmp_path):
    from app.services.ecom_set import products, service as svc

    user, model = await _setup(db_session, monkeypatch, tmp_path)
    row = await products.create_product(
        db_session, user, name="太平有象", category="scarf", size="135", material="silk_wool", notes="30%真丝+70%羊毛",
        colors=[("米白", _png(tmp_path / "c1.png").read_bytes(), "c1.png"),
                ("湖绿", _png(tmp_path / "c2.png", (60, 160, 150)).read_bytes(), "c2.png")],
        fabric=(_png(tmp_path / "f.png").read_bytes(), "f.png"),
    )
    out = products.to_out(row)
    assert [c["name"] for c in out["colors"]] == ["米白", "湖绿"] and out["fabric_image_url"]
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=None,
                                 notes="", category="general", frame="main_11", product_id=row.id, color_index=0,
                                 slots_picked=["scarf_cover", "scarf_back", "scarf_size"])
    o = svc.to_out(ecom, image_fen=95)
    assert o["category"] == "scarf" and o["styled"]["size"] == "135" and o["styled"]["material"] == "silk_wool"
    assert o["styled"]["color_name"] == "米白" and o["styled"]["fabric_url"] and o["styled"]["frame"] == "main_11"
    assert [i["slot"] for i in o["items"]] == ["scarf_cover", "scarf_back", "scarf_size"]
    assert all(i["ratio"] == "1:1" for i in o["items"]) and o["total_fen"] == 95 * 2
    assert o["items"][0]["title"] == "太平有象·丝毛方巾"

    again = await svc.recolor(db_session, user, ecom.id, 1)
    r = svc.to_out(again, image_fen=95)
    assert r["id"] != ecom.id and r["styled"]["color_name"] == "湖绿" and r["status"] == "planned"
    assert [i["scene"] for i in r["items"]] == [i["scene"] for i in o["items"]]

    with pytest.raises(ValueError, match="至少保留"):
        await products.remove_color(db_session, user, row.id, 0) and await products.remove_color(db_session, user, row.id, 0)


# 生成模特：打桩生图，存进「模特库」项目并设为默认
async def test_generate_model(db_session, monkeypatch, tmp_path):
    from app.services import ark as ark_mod, tool_models

    user, old = await _setup(db_session, monkeypatch, tmp_path)

    class FakeArk:
        async def gen_image(self, *a, **kw):
            return ark_mod.ImageResult(local_url="/static/generated/new_model.png")

    monkeypatch.setattr(ark_mod, "get_ark", lambda: FakeArk())
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", _noop_usage)
    asset = await tool_models.generate_model(db_session, user, description="25 岁中国女生，鹅蛋脸", make_default=True)
    rows = await tool_models.list_models(db_session, user.id)
    assert rows[0].id == asset.id and tool_models.is_default_model(rows[0])
    assert any(r.id == old.id and not tool_models.is_default_model(r) for r in rows)
    with pytest.raises(ValueError):
        await tool_models.generate_model(db_session, user, description="短")


async def _noop_usage(*a, **kw):
    return None


# 接口出参：options 的 styled / frames 与套图的 styled 字段不能被 schema 过滤掉
def test_schemas_keep_styled_fields():
    from app.schemas_ecom import EcomScarfOut, EcomSetOptionsOut

    opts = EcomSetOptionsOut(platforms=[], styles=[], counts=[], plan_fen=1, image_fen=1, billing_enabled=False,
                             styled=categories.options(), frames=[{"id": "main_11", "name": "主图"}])
    assert opts.styled and opts.frames[0].id == "main_11"
    assert EcomScarfOut(product_id=3, color_name="米白", frame="main_11").product_id == 3


# 上传模特照片：存进模特库并可设为默认
async def test_upload_model(db_session, monkeypatch, tmp_path):
    from app.services import tool_models

    user, old = await _setup(db_session, monkeypatch, tmp_path)
    asset = await tool_models.upload_model(db_session, user, data=_png(tmp_path / "m.png").read_bytes(),
                                           filename="m.png", make_default=True)
    rows = await tool_models.list_models(db_session, user.id)
    assert rows[0].id == asset.id and tool_models.is_default_model(rows[0]) and len(rows) == 2
    with pytest.raises(ValueError):
        await tool_models.upload_model(db_session, user, data=b"x", filename="m.txt")


# 实物图：按颜色款保存，规划时带上，提示词加说明
async def test_color_real_image(db_session, monkeypatch, tmp_path):
    from app.services.ecom_set import products, service as svc

    user, _ = await _setup(db_session, monkeypatch, tmp_path)
    row = await products.create_product(
        db_session, user, name="太平有象", category="scarf", size="135", material="silk_wool", notes="",
        colors=[("米白", _png(tmp_path / "c1.png").read_bytes(), "c1.png")], fabric=None,
        reals={0: (_png(tmp_path / "r.png").read_bytes(), "r.png")},
    )
    assert products.to_out(row)["colors"][0]["real_image_url"]
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=None,
                                 notes="", product_id=row.id, slots_picked=["scarf_cover"])
    assert svc.to_out(ecom, image_fen=80)["styled"]["real_url"]
    row = await products.set_color_real(db_session, user, row.id, 0, None)
    assert not products.to_out(row)["colors"][0]["real_image_url"]
    p = categories.build_prompt("scarf", slot_prompt="x", scene="", ref="model", size="135", material="silk_wool",
                                has_fabric=True, has_real=True)
    assert categories.REAL_HINT in p and p.endswith(categories.FABRIC_HINT)


# 改方案的条数上限要覆盖每个品类的全部可选槽位（丝巾官方预设就有 10 张）
def test_plan_update_accepts_every_selectable_slot():
    from app.schemas_ecom import EcomPlanUpdateIn

    assert len(presets.SCARF_COMPOSITION[10]) == 10
    for category in ("scarf", "apparel", "jewelry", "bag"):
        count = len(categories.slot_keys(category))
        body = EcomPlanUpdateIn(items=[{"idx": i, "scene": "画面"} for i in range(count)])
        assert len(body.items) == count
