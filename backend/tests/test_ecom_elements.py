"""电商图元素库与镜头库：系统预置数据、按资料推荐、每张错开分配、镜头摆位、提示词拼装、建套图与单张改元素。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import categories, presets, shots, variety
from app.services.ecom_set.elements import prompt_line
from app.services.ecom_set.elements_seed import CATEGORIES, system_elements


# 系统元素：key 唯一、分类合法、每类都有、总数够用
def test_system_elements_data():
    rows = system_elements()
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)) and len(rows) >= 120
    assert {r["category"] for r in rows} == set(CATEGORIES)
    assert all(r["name"] and r["prompt"] and r["industries"] for r in rows)


def _pool(n: int = 8) -> list[dict]:
    names = ["佛手柑", "红茶茶叶", "积雪草叶片", "洞石块", "亚麻布", "墙面树叶光影", "白色山茶花", "水珠"]
    cats = ["produce", "produce", "plant", "natural", "fabric", "light", "plant", "water"]
    return [{"key": f"k{i}", "name": names[i], "category": cats[i], "prompt": names[i], "image_url": "", "image_path": ""}
            for i in range(n)]


# 分配：镜头整套不重复、元素组合不重复；规划模型选的合法就用，原料静物优先原料类
def test_assign_variety():
    slots = [presets.SLOTS[k] for k in ("goods_cover", "goods_white", "goods_scene", "goods_selling", "goods_selling2",
                                        "goods_ingredient_still", "goods_flatlay", "goods_usage", "goods_texture")]
    planned = [{"shot": "low_hero", "elements": ["佛手柑", "洞石"]}] + [{} for _ in slots[1:]]
    out = variety.assign(slots, planned, _pool())
    shots_used = [v["shot"] for v in out.values() if "shot" in v]
    assert out["0"]["shot"] == "low_hero" and out["0"]["elements"][:2] == ["k0", "k3"]
    assert len(shots_used) == len(set(shots_used)) and out["6"]["shot"] == "flat_lay"
    combos = [tuple(sorted(v["elements"])) for v in out.values() if v.get("elements")]
    assert len(combos) == len(set(combos))
    assert "1" not in out  # 白底图不分配
    ing = out["5"]["elements"]
    assert len(ing) == 4 and sum(1 for k in ing if k in {"k0", "k1", "k2", "k6"}) >= 3
    assert len(out["8"]["elements"]) == 1  # 质地特写只放一个元素
    # 只选 3 个元素：每张 2 个，组合轮换不重复
    small = variety.assign([presets.SLOTS[k] for k in ("goods_cover", "goods_scene", "goods_selling")], [{}, {}, {}], _pool(3))
    combos3 = [tuple(sorted(v["elements"])) for v in small.values()]
    assert all(len(c) == 2 for c in combos3) and len(set(combos3)) == 3


# 镜头摆位：不超出画面、不压文字留白；平放/倾斜会改变抠图形状
def test_shot_placement_and_transform():
    for shot in shots.SHOTS.values():
        p = shots.placement(shot, height=0.5, blank="top", frame_ratio=0.75, aspect=0.32 if not shot.lay else 3.1)
        top = p.base - p.height
        assert top >= 0.31 and p.base <= 1.0 and 0 < p.cx < 1
    cut = Image.new("RGBA", (100, 300), (240, 240, 240, 255))
    lay = shots.transform(cut, shots.SHOTS["flat_lay"])
    assert lay.width > lay.height
    tilt = shots.transform(cut, shots.SHOTS["tilt_lean"])
    assert tilt.width > 100
    assert shots.transform(cut, None) is cut


# 提示词：选了元素时场景卡不带道具，本张元素与镜头写进提示词；实物照元素提示按参考图画
def test_prompt_assembly():
    info = {"scene": {"spec": {"backdrop": "素色墙", "surface": "洞石台", "props": ["橙子", "松针"]}}}
    with_props = categories.scene_of(info)
    skeleton = categories.scene_of(info, with_props=False)
    assert "橙子" in with_props and "橙子" not in skeleton and "洞石台" in skeleton
    els = [{"name": "佛手柑", "prompt": "佛手柑"}, {"name": "品牌礼盒", "prompt": "品牌礼盒", "image_url": "/static/x.png"}]
    line = prompt_line(els)
    assert "佛手柑" in line and "品牌礼盒请按后面附带的实物参考图画出" in line
    p = categories.build_goods_prompt(slot_prompt="卖点图", scene="", ref="composite", scene_card=skeleton, blank="top",
                                      material="tube", shot="正上方俯拍，商品平放在台面上（正俯平铺）", elements=line)
    assert "本张商品平放在台面上" in p and "佛手柑" in p
    hand = categories.build_goods_prompt(slot_prompt="手持", scene="", ref="hand", scene_card="", blank="top",
                                         material="tube")
    assert "手握持这件商品" in hand


def _tube() -> Image.Image:
    im = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 119, 359), 18, fill=(246, 244, 240, 255))
    return im


# 整合：系统元素推荐 → 我的元素（带实物照）→ 建套图选元素 → 每张错开 → 单张改镜头/元素 → 追加元素
async def test_elements_flow(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage
    from app.services.ecom_set import elements, products, service as svc
    from tests.conftest import make_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr(elements, "_SEEDED", True)
    for item in system_elements()[:60]:
        db_session.add(elements.EcomElement(scope="system", is_active=True, **item))
    await db_session.flush()
    user = await make_user(db_session)
    png = tmp_path / "tube.png"
    _tube().save(png)
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="", fabric=None, colors=[("No.04", png.read_bytes(), "a.png")])
    row.knowledge = {"variants": [{"name": "No.04", "top": "佛手柑、柠檬", "middle": "雪松", "base": "琥珀"}],
                     "ingredients": [{"name": "积雪草提取物"}]}
    await db_session.flush()
    rec = await elements.recommend(db_session, user, knowledge=row.knowledge, industry="daily")
    names = [r.name for r in rec]
    assert "佛手柑" in names and "积雪草叶片" in names
    mine = await elements.create_element(db_session, user, name="品牌礼盒", category="vessel", prompt="",
                                         image=(png.read_bytes(), "box.png"))
    assert mine.scope == "user" and mine.image_url
    listed, _ = await elements.list_elements(db_session, user, mine=True)
    assert [r.key for r in listed] == [mine.key]
    keys = [rec[0].key, rec[1].key, rec[2].key, mine.key]
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=8,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id, element_keys=keys)
    o = svc.to_out(ecom, image_fen=80)
    styled = (ecom.plan or {})["styled"]
    assert [e["key"] for e in styled["elements"]] == keys
    composite_items = [i for i in o["items"] if presets.SLOTS[i["slot"]].ref == "composite"]
    assert all(i["shot"] for i in composite_items)
    assert len({i["shot"] for i in composite_items}) == len(composite_items)
    flat = next(i for i in o["items"] if i["slot"] == "goods_flatlay")
    assert flat["shot"] == "flat_lay" and flat["elements"]
    # 单张改镜头与元素（元素只能来自本套元素池）
    scene_item = next(i for i in o["items"] if i["slot"] == "goods_scene")
    out = await svc.update_plan(db_session, user, ecom.id, [{"idx": scene_item["idx"], "title": "标题", "subtitle": "",
                                                           "bullets": [], "shot": "reflection",
                                                           "elements": [mine.key, "not-in-pool"]}])
    changed = next(i for i in svc.to_out(out, image_fen=80)["items"] if i["idx"] == scene_item["idx"])
    assert changed["shot"] == "reflection" and [e["key"] for e in changed["elements"]] == [mine.key]
    with pytest.raises(ValueError, match="镜头"):
        await svc.update_plan(db_session, user, ecom.id, [{"idx": scene_item["idx"], "shot": "nope"}])
    # 没选元素：日化自动用推荐 + 场景道具组成元素池
    auto = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id)
    assert (auto.plan or {})["styled"].get("elements")
    # 追加元素到套图元素池
    have = {e["key"] for e in (auto.plan or {})["styled"]["elements"]}
    extra = next(r["key"] for r in system_elements()[:60] if r["key"] not in have)
    grown = await svc.add_set_elements(db_session, user, auto.id, [extra])
    assert extra in [e["key"] for e in (grown.plan or {})["styled"]["elements"]]
    await elements.delete_element(db_session, user, mine.key)
    with pytest.raises(LookupError):
        await elements.delete_element(db_session, user, mine.key)
