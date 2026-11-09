"""电商图：字体风格排版、细节移植贴回、商品资料库（规整/合并/摘要/读图整理）、信息屏、切换字体风格。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.ecom_set import composite, infoscreens, knowledge as kn, typo
from app.services.ecom_set.layout import render


def _tube(seed: int = 0, w: int = 120, h: int = 360) -> Image.Image:
    """画一支带「标签文字」的白色软管（透明底）。"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 18, fill=(246, 244, 240, 255))
    rng = np.random.default_rng(seed)
    for _ in range(14):
        x0, y0 = int(rng.integers(15, w - 40)), int(rng.integers(30, h - 80))
        d.rectangle((x0, y0, x0 + int(rng.integers(8, 25)), y0 + int(rng.integers(3, 30))), fill=(40, 30, 25, 255))
    return im


# 三种字体风格都能排出图，且衬线/黑体版的文字比粗黑体版更细（深色像素更少）
@pytest.mark.parametrize("layout", ["title_sub", "title_bullets", "title_corner", "label_bar"])
def test_type_styles_render(tmp_path: Path, layout: str):
    base = tmp_path / "base.jpg"
    Image.new("RGB", (900, 1200), (236, 230, 220)).save(base)
    copy = {"title": "一抹柔润 香气留手", "subtitle": "乳木果油与植物精萃", "bullets": ["清爽不黏腻", "淡雅留香"],
            "en": "Soft & Fragrant"}
    dark = {}
    for st in typo.TYPE_STYLES:
        out = tmp_path / f"{st}.jpg"
        render(base, out, size=(790, 1053), layout=layout, blank="top", copy=copy, type_style=st)
        a = np.asarray(Image.open(out).convert("L"))
        dark[st] = int((a < 90).sum())
        assert dark[st] > 0
    if layout != "label_bar":  # 标签条：粗黑体是深底白字，不可比
        assert dark["serif"] < dark["bold"] and dark["sans"] < dark["bold"]


# 折行：英文单词不被拆开，放不下时加省略号
def test_typo_wrap_keeps_words():
    f = typo.font("sans", False, 40)
    lines = typo.wrap("Van Reed 香氛护手霜 Bois de Balincourt", f, 0.05, 420, 3)
    assert all(not ln.endswith(("Va", "Re", "Bal")) for ln in lines)
    assert "".join(lines).replace(" ", "") == "VanReed香氛护手霜BoisdeBalincourt"
    short = typo.wrap("这是一段很长很长很长很长很长很长的文字", f, 0.0, 200, 1)
    assert len(short) == 1 and short[0].endswith("…")


# 默认字体风格：日化按场景行业（食品/3C 黑体，其余衬线），其它品类保持粗黑体
def test_default_type_style():
    assert typo.default_type_style("goods", {"industries": ["daily"]}) == "serif"
    assert typo.default_type_style("goods", {"industries": ["food"]}) == "sans"
    assert typo.default_type_style("goods", None) == "serif"
    assert typo.default_type_style("scarf", {"industries": ["daily"]}) == "bold"
    assert typo.normalize_type_style("nope") == "bold"


# 细节移植：商品明暗跟随 AI（左亮右暗的侧光），标签文字仍是原图
def test_harmonize_follows_ai_light():
    cut = _tube(2)
    w, h = cut.size
    ramp = np.linspace(250, 150, w)[None, :, None] * np.ones((h, 1, 3))
    ai_patch = Image.fromarray(ramp.astype(np.uint8))
    out = composite.harmonize(cut, ai_patch)
    a = np.asarray(out.convert("RGB"), dtype=np.float32)
    mask = np.asarray(cut.getchannel("A")) > 200
    left = a[:, : w // 3][mask[:, : w // 3]].mean()
    right = a[:, 2 * w // 3:][mask[:, 2 * w // 3:]].mean()
    assert left - right > 40
    # 文字（深色块）依然清楚
    assert np.asarray(out.convert("L"))[mask].min() < 80


# 资料规整：裁字数、限条数、去空项；合并去重；摘要包含各类信息
def test_knowledge_normalize_merge_digest():
    raw = {"summary": "x" * 100, "selling_points": [{"title": "清爽", "desc": "不黏"}, {"title": ""}, "bad"] * 5,
           "usage": ["取适量", "取适量", ""], "variants": [{"name": "No.04 秘密森林", "top": "柑橘"}]}
    k = kn.normalize(raw)
    assert len(k["summary"]) == 60 and k["usage"] == ["取适量"]
    assert k["selling_points"] == [{"title": "清爽", "desc": "不黏"}]
    m = kn.merge([k, {"selling_points": [{"title": "清爽", "desc": "x"}, {"title": "留香"}], "story": "品牌故事"}])
    assert [r["title"] for r in m["selling_points"]] == ["清爽", "留香"] and m["story"] == "品牌故事"
    d = kn.digest(m)
    assert "卖点：清爽" in d and "No.04 秘密森林（柑橘）" in d and "用法：取适量" in d
    assert kn.is_empty({}) and kn.digest({}) == ""


# 资料里的极限词只提示不改字
def test_knowledge_compliance_warnings():
    w = kn.compliance_warnings({"selling_points": [{"title": "最好用的护手霜"}]})
    assert w and "最" in w[0]


# 长详情图按高度切片
def test_slice_long_image(tmp_path: Path):
    p = tmp_path / "long.jpg"
    Image.new("RGB", (790, 5000), (250, 250, 250)).save(p)
    uris = kn.slice_image(p)
    assert len(uris) == 4 and all(u.startswith("data:image/jpeg;base64,") for u in uris)


# 信息屏：各类都能排出来；资料不足时判定为不可用
def test_info_screens_render(tmp_path: Path):
    cut = tmp_path / "c.png"
    _tube(1).save(cut)
    k = kn.normalize({
        "selling_points": [{"title": "清爽不黏腻", "desc": "质地轻薄"}, {"title": "淡雅留香", "desc": "通勤友好"}],
        "ingredients": [{"name": "乳木果油", "desc": "滋润"}], "usage": ["取适量", "揉开"],
        "variants": [{"name": "No.04 秘密森林", "top": "柑橘", "middle": "雪松", "base": "琥珀"},
                     {"name": "No.05 冰雪玫瑰", "top": "玫瑰"}],
        "specs": [{"label": "规格", "value": "60g"}], "story": "源自巴黎的香氛品牌。",
    })
    styled = {"knowledge": k, "variants": [{"name": "No.04", "image_path": str(cut)}, {"name": "No.05", "image_path": str(cut)}],
              "product_path": str(cut), "product_name": "香氛护手霜", "size": "60g", "material_label": "软管"}
    for slot in infoscreens.INFO_SLOTS:
        assert infoscreens.available(slot, k, styled["variants"])
        out = infoscreens.render_info(slot, tmp_path / f"{slot}.jpg", size=(790, 1053), copy={}, styled=styled,
                                      type_style="serif")
        a = np.asarray(Image.open(out).convert("L"))
        assert (a < 120).sum() > 500  # 有文字
    assert not infoscreens.available("goods_info_ingredients", {}, [])
    assert infoscreens.available("goods_info_specs", {}, [])
    rows = infoscreens.spec_rows(styled)
    assert ("规格", "60g") in rows and ("品名", "香氛护手霜") in rows


# 整合：上传参考详情 → 整理资料（mock）→ 12 张套图带信息屏 → 切换字体风格只重排字
async def test_knowledge_flow(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
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
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="", fabric=None,
                                        colors=[("No.04", png.read_bytes(), "a.png"), ("No.05", png.read_bytes(), "b.png")])
    detail = tmp_path / "detail.jpg"
    Image.new("RGB", (790, 3000), (250, 250, 250)).save(detail)
    with pytest.raises(ValueError, match="参考详情"):
        await products.extract_knowledge(db_session, user, row.id)
    row = await products.add_references(db_session, user, row.id, kind="own", files=[(detail.read_bytes(), "d.jpg")])
    row = await products.extract_knowledge(db_session, user, row.id)
    out = products.to_out(row)
    assert out["references"][0]["kind"] == "own" and out["knowledge"]["selling_points"]
    # 用户修改资料
    k = dict(out["knowledge"])
    k["story"] = "源自巴黎。"
    row = await products.set_knowledge(db_session, user, row.id, k)
    assert products.to_out(row)["knowledge"]["story"] == "源自巴黎。"

    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=12,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id, color_index=0)
    slots = [it.slot for it in ecom.items]
    assert "goods_info_points" in slots and "goods_info_specs" in slots and "goods_info_usage" in slots
    # mock 资料里没有成分 → 成分说明被跳过并提示
    assert "goods_info_ingredients" not in slots
    assert any("成分说明" in w for w in (ecom.plan or {})["warnings"])
    o = svc.to_out(ecom, image_fen=95)
    assert o["styled"]["type_style"] == "serif"
    assert all(not i["billable"] for i in o["items"] if i["slot"].startswith("goods_info_"))
    started = await svc.start_generation(db_session, user, ecom.id)
    info = [it for it in started.items if it.slot.startswith("goods_info_")]
    assert info and all(it.status == "succeeded" and it.final_url for it in info)
    before = {it.idx: it.final_url for it in info}
    switched = await svc.set_type_style(db_session, user, ecom.id, "sans")
    assert svc.to_out(switched, image_fen=95)["styled"]["type_style"] == "sans"
    assert all(it.final_url != before[it.idx] for it in switched.items if it.idx in before)
    with pytest.raises(ValueError):
        await svc.set_type_style(db_session, user, ecom.id, "comic")
    # 改文案不带 en 时保留英文小标
    it = info[0]
    it_copy = dict(it.copy_json or {})
    it.copy_json = {**it_copy, "en": "Highlights"}
    await db_session.flush()
    edited = await svc.update_copy(db_session, user, ecom.id, it.idx, {"title": "新标题", "subtitle": "", "bullets": []})
    assert next(x for x in edited.items if x.idx == it.idx).copy_json["en"] == "Highlights"


# 回归：多批读图里同一款香型写法不同（中文名 / 中英混合 / 只有英文）要合并成一款，字段互补，名称优先中文
def test_variants_merge_by_number():
    parts = [{"variants": [{"name": "No.03 伯爵红茶", "desc": "红茶香"}, {"name": "No.04 Bois de Balinc"}]},
             {"variants": [{"name": "No.03 伯爵红茶 The Earl", "top": "红茶", "middle": "茶叶", "base": "麝香"}]}]
    v = kn.merge(parts)["variants"]
    assert len(v) == 2
    assert v[0] == {"name": "No.03 伯爵红茶", "top": "红茶", "middle": "茶叶", "base": "麝香", "desc": "红茶香"}
    variants = [{"name": "No.04 秘密森林", "image_path": ""}]
    assert infoscreens.display_name("No.04 Bois de Balinc", variants) == "No.04 秘密森林"
    assert infoscreens.display_name("No.02 高山树林 Le Long F", variants) == "No.02 高山树林"


# 产品信息表：同一个值（60g / 60g（2.0 fl.oz.））只保留一行
def test_spec_rows_dedupe():
    styled = {"knowledge": kn.normalize({"specs": [{"label": "净含量", "value": "60g（2.0 fl.oz.）"},
                                                   {"label": "单支净含量", "value": "60g"},
                                                   {"label": "保质期", "value": "3年"}]}),
              "product_name": "护手霜", "size": "60g"}
    rows = infoscreens.spec_rows(styled)
    assert [r[0] for r in rows] == ["净含量", "保质期", "品名"]
    assert all(r[0] != "注意事项" for r in infoscreens.spec_rows({**styled, "knowledge": {**styled["knowledge"], "cautions": ["避光"]}}, with_cautions=False))


# 使用方法：长句在第一个逗号处拆成「短标题 + 说明」，拆不开的整句保留
def test_split_step():
    assert infoscreens.split_step("洁净手部后，取适量手霜均匀涂抹") == ("洁净手部后", "取适量手霜均匀涂抹")
    assert infoscreens.split_step("取适量于手背") == ("取适量于手背", "")
    assert infoscreens.split_step("干燥粗糙倒刺区域可沿甲缘涂抹一层后轻轻按揉") == ("", "干燥粗糙倒刺区域可沿甲缘涂抹一层后轻轻按揉")
    v = [{"name": "No.12 布斯瓦勒", "image_path": ""}]
    assert infoscreens.display_name("No.12 Bousval 布斯瓦勒", v) == "No.12 布斯瓦勒"


# 贴合度：AI 画的商品与原图同角度时高，画歪（旋转）时低于阈值、不贴回
def test_match_score_detects_tilt():
    cut = _tube(4)
    stage, boxes = composite.place_products([cut], composite.lineup_placements(1, height=0.5), (600, 800), (216, 195, 168))
    W, H = stage.size
    good = Image.new("RGBA", (W, H), (200, 185, 160, 255))
    x, y, w, h = boxes[0]
    good.alpha_composite(cut.resize((w, h)), (x, y))
    tilted = Image.new("RGBA", (W, H), (200, 185, 160, 255))
    rot = cut.resize((w, h)).rotate(14, expand=True)
    tilted.alpha_composite(rot, (x - (rot.width - w) // 2, y - (rot.height - h) // 2))
    assert composite.match_score(good.convert("RGB"), cut, boxes[0]) > 0.6
    assert composite.match_score(tilted.convert("RGB"), cut, boxes[0]) < composite.MATCH_MIN


# GPT 套图里质地特写固定走 Seedream
def test_texture_uses_seedream_in_gpt_set():
    from app.models_ecom import EcomSet, EcomSetItem
    from app.services.ecom_set import service

    ecom = EcomSet(plan={"styled": {"image_engine": "gpt-image-2"}})
    assert service.item_engine(ecom, EcomSetItem(slot="goods_texture")) == "seedream"
    assert service.item_engine(ecom, EcomSetItem(slot="goods_scene")) == "gpt-image-2"
    assert service.item_engine(EcomSet(plan={}), EcomSetItem(slot="goods_scene")) == "seedream"
