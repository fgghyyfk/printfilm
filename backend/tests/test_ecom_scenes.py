"""电商图场景库与标品合成：场景卡、系统场景数据、标品提示词、摆位/定位/贴回、白底图。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from app.services.ecom_set import categories, composite, presets, scenes
from app.services.ecom_set.scenes_seed import SYSTEM_SCENES


# 场景卡字段规整：截断、道具列表、底色与占比钳制
def test_normalize_spec():
    s = scenes.normalize_spec({"props": "石头，松针、橙子", "bg_color": "#zzz", "product_ratio": 2, "blank": "x"})
    assert s["props"] == ["石头", "松针", "橙子"]
    assert s["bg_color"] == scenes.DEFAULT_BG and s["product_ratio"] == 0.7 and s["blank"] == "top"


# 场景描述拼装；手写提示词优先
def test_scene_text():
    t = scenes.scene_text({"backdrop": "素色墙", "surface": "洞石台", "props": ["松针"], "light": "左侧窗光"})
    assert "素色墙" in t and "洞石台" in t and "松针" in t and "比例真实" in t
    assert scenes.scene_text({"prompt": "海边礁石，傍晚逆光", "backdrop": "x"}) == "海边礁石，傍晚逆光"


# 系统场景：45 个、key 唯一、行业合法、字段齐全
def test_system_scenes_data():
    keys = [s["key"] for s in SYSTEM_SCENES]
    assert len(keys) == 45 and len(set(keys)) == 45
    for s in SYSTEM_SCENES:
        assert s["industries"] and set(s["industries"]) <= set(scenes.INDUSTRIES)
        spec = scenes.normalize_spec(s["spec"])
        assert spec["backdrop"] and spec["surface"] and spec["light"]
    for ind in scenes.INDUSTRIES:
        assert sum(ind in s["industries"] for s in SYSTEM_SCENES) >= 5


# 快照只保存需要的字段
def test_snapshot():
    from app.models_ecom import EcomScene

    row = EcomScene(key="k", name="自然原木石", industries=["daily"], spec={"surface": "洞石台"})
    snap = scenes.snapshot(row)
    assert snap["key"] == "k" and snap["spec"]["surface"] == "洞石台"
    assert scenes.snapshot(None) is None


# 标品提示词：合成槽位要求保持商品不变并带上场景卡；质地槽位不带商品
def test_goods_prompts():
    info = {"scene": {"key": "k", "name": "n", "spec": {"surface": "洞石台", "props": ["松针"]}}}
    card = categories.scene_of(info)
    p = categories.build_prompt("goods", slot_prompt="主视觉", scene="平视", ref="composite", size=None,
                                material="tube", has_fabric=False, scene_card=card, blank="top")
    assert p.startswith("以参考图中已经摆放好的商品为主体") and "洞石台" in p and "软管" in p and "留白" in p
    t = categories.build_prompt("goods", slot_prompt="质地", scene="", ref="texture", size=None,
                                material="tube", has_fabric=False, scene_card=card)
    assert "不要出现任何包装" in t and "已经摆放好" not in t
    # 纯商品槽位跟随场景卡，模特槽位不受影响
    q = categories.build_prompt("jewelry", slot_prompt="主图", scene="", ref="product", size="", material="gold",
                                has_fabric=False, scene_card=card)
    m = categories.build_prompt("jewelry", slot_prompt="佩戴", scene="", ref="model", size="", material="gold",
                                has_fabric=False, scene_card=card)
    assert "洞石台" in q and "洞石台" not in m


# 标品槽位与构成
def test_goods_slots():
    keys = categories.composition("goods", 8)
    assert "goods_lineup" in keys and all(k in presets.SLOTS for k in keys)
    assert presets.SLOTS["goods_white"].ref == "flat" and presets.SLOTS["goods_cover"].ref == "composite"
    assert presets.output_size("taobao_34", "goods_cover") == (1440, 1920)


def _tube(seed: int, w: int = 120, h: int = 380) -> Image.Image:
    """造一支带标签纹理的透明底「软管」。"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 18, fill=(246, 244, 240, 255))
    rng = np.random.default_rng(seed)
    for _ in range(14):
        x0, y0 = int(rng.integers(15, w - 40)), int(rng.integers(30, h - 80))
        d.rectangle((x0, y0, x0 + int(rng.integers(8, 25)), y0 + int(rng.integers(3, 30))), fill=(40, 30, 25, 255))
    return im


def _fake_ai(size, cutouts, boxes, *, scale=1.06, dx=18, dy=-40):
    """模拟 AI：背景换成有纹理的场景，商品被平移、略放大，并带一点色偏。"""
    W, H = size
    rng = np.random.default_rng(0)
    noise = (rng.random((H, W, 3)) * 40 + np.array([180, 160, 130])).astype(np.uint8)
    ai = Image.fromarray(noise).convert("RGBA")
    out = []
    for cut, (x, y, w, h) in zip(cutouts, boxes):
        nw, nh = int(w * scale), int(h * scale)
        p = cut.resize((nw, nh))
        ai.alpha_composite(p, (x + dx, y + dy))
        out.append((x + dx, y + dy, nw, nh))
    return ai.convert("RGB"), out


# 单支：AI 平移 + 放大后仍能准确找到并贴回
def test_locate_single():
    cut = _tube(1)
    stage, boxes = composite.place_products([cut], composite.lineup_placements(1, height=0.5), (600, 800), (216, 195, 168))
    ai, truth = _fake_ai(stage.size, [cut], boxes)
    found = composite.locate(ai, [cut], boxes)
    (fx, fy, fw, fh), (tx, ty, tw, th) = found[0], truth[0]
    assert abs(fx - tx) <= 6 and abs(fy - ty) <= 6 and abs(fh - th) <= th * 0.05


# 多支陈列：AI 重排间距后，按从左到右顺序对应
def test_locate_lineup_order():
    cuts = [_tube(i) for i in range(4)]
    pls = composite.lineup_placements(4, height=0.4)
    stage, boxes = composite.place_products(cuts, pls, (1000, 750), (216, 195, 168))
    # 模拟 AI 把间距收窄：整体向中间挤
    squeezed = [(int(500 + (x - 500) * 0.8), y, w, h) for x, y, w, h in boxes]
    ai, truth = _fake_ai(stage.size, cuts, squeezed, scale=1.0, dx=0, dy=0)
    found = composite.locate(ai, cuts, boxes)
    xs = [f[0] for f in found]
    assert xs == sorted(xs)
    for f, t in zip(found, truth):
        assert abs(f[0] - t[0]) <= 8 and abs(f[1] - t[1]) <= 8


# 贴回：商品区域的细节来自原图（文字边缘对比保留），光色跟随场景
def test_restore_keeps_detail():
    cut = _tube(3)
    stage, boxes = composite.place_products([cut], composite.lineup_placements(1, height=0.5), (600, 800), (216, 195, 168))
    ai, truth = _fake_ai(stage.size, [cut.resize((cut.width, cut.height))], boxes, scale=1.0, dx=0, dy=0)
    blurred_ai = ai.copy()
    x, y, w, h = truth[0]
    blurred_ai.paste(ai.crop((x, y, x + w, y + h)).resize((w // 6, h // 6)).resize((w, h)), (x, y))
    out = composite.restore(blurred_ai, [cut], [truth[0]])
    region = np.asarray(out.crop((x, y, x + w, y + h)).convert("L"), dtype=np.float32)
    assert region.std() > 25  # 原图的标签细节回来了


# 白底主图与透明检测、铺白
def test_white_and_alpha(tmp_path: Path):
    p = tmp_path / "t.png"
    _tube(5).save(p)
    assert composite.has_alpha(p)
    flat = composite.flatten_white(p)
    assert flat.mode == "RGB" and flat.getpixel((0, 0)) == (255, 255, 255)
    j = tmp_path / "t.jpg"
    flat.save(j)
    assert not composite.has_alpha(j)
    white = composite.render_white(composite.load_cutout(p), (1440, 1920))
    assert white.size == (1440, 1920) and white.getpixel((5, 5)) == (255, 255, 255)


# 集成：日化商品（透明底 PNG）+ 选场景 → 规划快照场景、槽位为标品槽位；开始生成时白底图免费直接出
async def test_goods_plan_with_scene(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.models_ecom import EcomScene
    from app.services import storage
    from app.services.ecom_set import products, service as svc
    from tests.conftest import make_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db_session)
    png = tmp_path / "tube.png"
    _tube(7).save(png)
    db_session.add(EcomScene(key="t_wood", scope="system", name="自然原木石", industries=["daily"],
                             spec={"surface": "洞石台", "props": ["松针"]}, is_active=True))
    await db_session.flush()
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="7 款香型", colors=[("No.04", png.read_bytes(), "tube.png")], fabric=None)
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id, color_index=0,
                                 scene_key="t_wood")
    o = svc.to_out(ecom, image_fen=95)
    assert o["category"] == "goods" and o["styled"]["scene_key"] == "t_wood" and o["styled"]["scene_name"] == "自然原木石"
    assert [i["slot"] for i in o["items"]] == list(categories.composition("goods", 6))
    assert (ecom.plan or {})["styled"]["scene"]["spec"]["surface"] == "洞石台"
    started = await svc.start_generation(db_session, user, ecom.id)
    white = next(it for it in started.items if it.slot == "goods_white")
    assert white.status == "succeeded" and white.final_url and white.task_run_id is None
    import pytest as _pytest

    with _pytest.raises(ValueError, match="场景不存在"):
        await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=6,
                              notes="", category="goods", frame="taobao_34", product_id=row.id, scene_key="nope")
