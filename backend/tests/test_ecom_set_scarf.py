"""丝巾详情页套图：尺寸/张数规整、槽位构成、提示词、免模型版式、规划与生成流程（ARK_MOCK）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.models_drama import DramaAsset, DramaProject
from app.services.ecom_set import presets, scarf
from tests.conftest import make_user


def _scarf_png(path: Path) -> Path:
    img = Image.new("RGB", (600, 600), (240, 200, 210))
    for x in range(0, 600, 60):
        for y in range(0, 600, 60):
            img.paste((40, 150, 130) if (x + y) // 60 % 2 else (230, 120, 90), (x, y, x + 30, y + 30))
    img.save(path)
    return path


# 尺寸与张数：未知值回落默认
def test_normalize_size_and_count():
    assert scarf.normalize_size("110cm") == "110" and scarf.normalize_size("999") == scarf.DEFAULT_SIZE
    assert scarf.normalize_count(10) == 10 and scarf.normalize_count(7) == scarf.DEFAULT_COUNT
    assert "110×110" in scarf.size_label("110")


# 槽位构成：6/8/10 张，封面在第一位，尺寸图为免模型槽位，输出统一 3:4
@pytest.mark.parametrize("count", [6, 8, 10])
def test_scarf_slots(count):
    slots = presets.slots_for("scarf", count)
    assert len(slots) == count and slots[0].key == "scarf_cover"
    assert any(s.key == "scarf_size" and s.ref == "flat" for s in slots)
    assert all(presets.output_size("taobao", s.key) == scarf.OUTPUT_SIZE for s in slots)
    assert all(presets.gen_ratio("taobao", s.key) == "3:4" for s in slots)
    assert [s.key for s in presets.slots_for("general", 6)] == list(presets.SET_COMPOSITION[6])


# 提示词：模特槽位带身份约束与尺寸系法，面料槽位只说平铺参考
def test_build_prompt():
    p = scarf.build_prompt(slot_prompt="颈前打结", scene="窗边", ref="model", size="68")
    assert p.startswith("参考图1是固定模特") and "68×68cm" in p and "不重画图案" in p
    q = scarf.build_prompt(slot_prompt="面料特写", scene="", ref="product", size="68")
    assert q.startswith("参考图是丝巾的完整平铺图") and "固定模特" not in q
    assert "规格：110×110cm" in scarf.plan_notes("110", "牡丹亭") and scarf.plan_notes("110", "牡丹亭").endswith("牡丹亭")


# 免模型版式：尺寸图与平铺图输出 3:4 成图；封面竖排标题可排版
def test_flat_renders_and_vertical_title(tmp_path):
    from app.services.ecom_set import layout

    product = _scarf_png(tmp_path / "flat.png")
    size_out = scarf.render_size_spec(product, tmp_path / "size.jpg", size_id="88",
                                      copy={"title": "尺寸规格", "bullets": ["桑蚕丝", "手工卷边"]})
    flat_out = scarf.render_flat(product, tmp_path / "flat.jpg", copy={"title": "牡丹亭", "subtitle": "平铺原图"})
    for out in (size_out, flat_out):
        with Image.open(out) as im:
            assert im.size == scarf.OUTPUT_SIZE
    cover = layout.render(product, tmp_path / "cover.jpg", size=scarf.OUTPUT_SIZE, layout="vertical_title",
                          blank="none", copy={"title": "牡丹亭·真丝方巾", "subtitle": "桑蚕丝 原创轻奢"})
    with Image.open(cover) as im:
        assert im.size == scarf.OUTPUT_SIZE


# 端到端（ARK_MOCK）：没有默认模特时报错；设默认后规划→生成，尺寸图免费直出，模特槽位入队
async def test_scarf_plan_and_start(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage, tool_models
    from app.services.ecom_set import service as svc

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    user = await make_user(db_session)
    project = DramaProject(user_id=user.id, title="模特", params={})
    db_session.add(project)
    await db_session.flush()
    model = DramaAsset(project_id=project.id, type="image", asset_type="image", name="模特A",
                       url="/static/generated/model_a.png", params={})
    db_session.add(model)
    await db_session.flush()
    data = _scarf_png(tmp_path / "upload.png").read_bytes()

    with pytest.raises(ValueError, match="选择模特"):
        await svc.create_plan(db_session, user, data=data, filename="s.png", name="牡丹亭", platform="taobao",
                              style="minimal", count=6, notes="", category="scarf", scarf_size="88")
    await tool_models.update_model(db_session, user.id, model.id, is_model_flag=True, is_default=True)
    ecom = await svc.create_plan(db_session, user, data=data, filename="s.png", name="牡丹亭", platform="taobao",
                                 style="minimal", count=6, notes="", category="scarf", scarf_size="88")
    out = svc.to_out(ecom, image_fen=95)
    assert out["category"] == "scarf" and out["scarf"]["model_asset_id"] == model.id and out["scarf"]["size"] == "88"
    assert [i["slot"] for i in out["items"]] == list(presets.SCARF_COMPOSITION[6])
    assert out["total_fen"] == 95 * 5                               # 尺寸图不收费

    ecom = await svc.start_generation(db_session, user, ecom.id)
    by_slot = {it.slot: it for it in ecom.items}
    assert by_slot["scarf_size"].status == "succeeded" and by_slot["scarf_size"].final_url
    assert by_slot["scarf_size"].task_run_id is None
    assert all(it.status == "queued" and it.task_run_id for k, it in by_slot.items() if k != "scarf_size")


# 封面文案：款名 + 真丝方巾，副标题为规格
def test_cover_copy():
    c = scarf.cover_copy("国风方巾", "110")
    assert c["title"] == "国风·真丝方巾" and c["subtitle"] == "真丝 110cm"
    assert scarf.cover_copy("牡丹亭", "88")["title"] == "牡丹亭·真丝方巾"


# 丝毛材质：质感改为哑光呢面、造型改秋冬；135cm 尺寸可用；封面写「丝毛方巾」
def test_silk_wool_material_and_135():
    assert scarf.normalize_size("135") == "135" and "135×135" in scarf.size_label("135")
    p = scarf.build_prompt(slot_prompt="背面披肩", scene="", ref="model", size="135", material="silk_wool")
    assert "丝毛混纺" in p and "人字斜纹" in p and "丝光" in p and "无噪点" in p and "羊绒针织" in p and "真丝吊带" not in p
    assert "斜纹缎面" in scarf.build_prompt(slot_prompt="x", scene="", ref="product", size="88")
    c = scarf.cover_copy("《太平有象》", "135", "silk_wool")
    assert c["title"] == "太平有象·丝毛方巾" and c["subtitle"] == "丝毛混纺 135cm"
    assert "丝毛混纺" in scarf.plan_notes("135", "", "silk_wool")


# 纹样细节：从平铺图裁局部，输出 3:4
def test_render_crop(tmp_path):
    out = scarf.render_crop(_scarf_png(tmp_path / "flat.png"), tmp_path / "crop.jpg", copy={"title": "纹样细节"})
    with Image.open(out) as im:
        assert im.size == scarf.OUTPUT_SIZE
    assert presets.SCARF_COMPOSITION[10].count("scarf_crop") == 1
