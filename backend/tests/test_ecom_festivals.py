"""电商图节日主题与分类选择：节日日期、临近排序、场景分组、元素包、节日场景出方案。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from app.models_ecom import EcomScene
from app.services.ecom_set import festivals, scenes
from app.services.ecom_set.elements_seed import system_elements
from app.services.ecom_set.festivals_seed import FESTIVAL_SCENES
from app.services.ecom_set.packs import STYLE_PACKS, style_pack_keys
from app.services.ecom_set.scenes_seed import SCENE_GROUPS, SYSTEM_SCENES, group_of

TODAY = date(2026, 11, 10)


# 日期：农历、第 N 个周日、跨年、年货节区间
def test_festival_dates():
    occ = lambda k, y: festivals.occurrence(festivals.BY_KEY[k], y)  # noqa: E731
    assert occ("chunjie", 2027)[1] == date(2027, 2, 6)
    assert occ("zhongqiu", 2028)[1] == date(2028, 10, 3)
    assert occ("mother", 2027)[1] == date(2027, 5, 9) and occ("father", 2027)[1] == date(2027, 6, 20)
    assert occ("nianhuo", 2027)[0] == date(2026, 12, 26) and occ("nianhuo", 2027)[2] == date(2027, 1, 30)
    yd = festivals.next_occurrence(festivals.BY_KEY["yuandan"], TODAY)
    assert yd["date"] == "2027-01-01" and not yd["active"] and yd["days"] == 52
    d11 = festivals.next_occurrence(festivals.BY_KEY["d1111"], TODAY)
    assert d11["active"] and d11["days"] == 1
    # 双11 过后下一次是明年
    assert festivals.next_occurrence(festivals.BY_KEY["d1111"], date(2026, 11, 12))["date"] == "2027-11-11"
    # 超出农历表的年份也能给出近似日期，不报错
    assert festivals.occurrence(festivals.BY_KEY["chunjie"], 2040)[1].month in (1, 2)
    order = [f.key for f in festivals.ordered(TODAY)]
    assert order.index("d1111") < order.index("xmas") < order.index("chunjie") < order.index("d618")


# 数据：每个节日的元素包都存在、节日场景都挂到节日、普通场景都有风格组
def test_festival_data():
    keys = {r["key"] for r in system_elements()}
    assert len(keys) == len(system_elements())
    for f in festivals.FESTIVALS:
        assert f.type in festivals.FESTIVAL_TYPES and len(f.pack) >= 5
        assert all(k in keys for k in festivals.pack_keys(f)), f.key
    in_packs = {k for f in festivals.FESTIVALS for k in festivals.pack_keys(f)}
    assert {r["key"] for r in system_elements() if r["category"] == "festival"} <= in_packs  # 节日道具都能在节日页签里找到
    for k in STYLE_PACKS:
        assert all(x in keys for x in style_pack_keys(k)), k
    fest_keys = [s["key"] for s in FESTIVAL_SCENES]
    assert len(fest_keys) == len(set(fest_keys)) == 26
    assert not set(fest_keys) & {s["key"] for s in SYSTEM_SCENES}
    linked = {s for f in festivals.FESTIVALS for s in f.scenes}
    assert set(fest_keys) <= linked
    assert all(group_of(s["key"]) in SCENE_GROUPS for s in SYSTEM_SCENES)
    assert all(scenes.normalize_spec(s["spec"])["surface"] for s in FESTIVAL_SCENES)
    note = festivals.copy_note(festivals.BY_KEY["chunjie"])
    assert "不要编造折扣" in note and "HAPPY NEW YEAR" in note


def _row(key: str, scope: str = "system") -> EcomScene:
    return EcomScene(key=key, scope=scope, name=key, industries=["daily"], spec={}, is_active=True)


# 场景列表编排：全部 = 临近节日在前 + 普通场景；节日组按日期；按节日类型、风格组筛选
def test_arrange_groups():
    rows = [_row("daily_wood_stone"), _row("daily_water"), _row("fest_618"),
            _row("fest_1212"), _row("fest_chunjie"), _row("daily_holiday"), _row("u1", "user")]
    names = lambda rs: [r.key for r in rs]  # noqa: E731
    allv = scenes._arrange(rows, group="", festival_type="", today=TODAY)
    assert names(allv)[:2] == ["fest_1212", "fest_chunjie"] and "fest_618" not in names(allv)
    assert {"daily_wood_stone", "daily_water", "daily_holiday", "u1"} <= set(names(allv))
    fest = scenes._arrange(rows, group="festival", festival_type="", today=TODAY)
    assert names(fest) == ["fest_1212", "fest_chunjie", "fest_618", "daily_holiday"]
    assert names(scenes._arrange(rows, group="festival", festival_type="traditional", today=TODAY)) == ["fest_chunjie"]
    assert names(scenes._arrange(rows, group="water", festival_type="", today=TODAY)) == ["daily_water"]
    assert names(scenes._arrange(rows, group="mine", festival_type="", today=TODAY)) == ["u1"]
    out = scenes.to_out(_row("fest_chunjie"), TODAY)
    assert out["group"] == "festival" and out["festival"]["key"] == "chunjie" and out["festival"]["soon"]


def _tube() -> Image.Image:
    im = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 119, 359), 18, fill=(246, 244, 240, 255))
    return im


# 整合：元素包接口、节日场景推荐、选节日场景不挑元素时自动用节日包，文案带节日语气
async def test_festival_flow(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import storage
    from app.services.ecom_set import elements, packs, products, service as svc
    from tests.conftest import make_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    monkeypatch.setattr("app.services.ecom_set.jobs.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr("app.services.ecom_set.service.public_url", lambda path: f"/static/test/{Path(path).name}")
    monkeypatch.setattr(elements, "_SEEDED", True)
    for item in system_elements():
        db_session.add(elements.EcomElement(scope="system", is_active=True, **item))
    spec = next(s for s in FESTIVAL_SCENES if s["key"] == "fest_chunjie")
    db_session.add(EcomScene(key="fest_chunjie", scope="system", name=spec["name"], industries=spec["industries"],
                             spec=spec["spec"], is_active=True))
    await db_session.flush()
    user = await make_user(db_session)

    out = await packs.list_packs(db_session, user, scene_key="fest_chunjie", today=TODAY)
    assert out["scene_pack"] == "fest_chunjie"
    kinds = [p["kind"] for p in out["packs"]]
    assert kinds[: len(STYLE_PACKS)] == ["style"] * len(STYLE_PACKS)
    first_fest = next(p for p in out["packs"] if p["kind"] == "festival")
    assert first_fest["festival"]["active"] and all(p["elements"] for p in out["packs"])

    rec = await elements.recommend(db_session, user, knowledge=None, industry="daily", scene_key="fest_chunjie")
    assert [r.key for r in rec[:6]] == festivals.pack_keys(festivals.BY_KEY["chunjie"])[:6]

    seen: dict = {}
    from app.services.ecom_set import styled as styled_mod

    real_plan = styled_mod.plan_set

    async def spy(**kw):
        seen["extra"] = kw.get("extra") or ""
        return await real_plan(**kw)

    monkeypatch.setattr(styled_mod, "plan_set", spy)
    png = tmp_path / "tube.png"
    _tube().save(png)
    row = await products.create_product(db_session, user, name="香氛护手霜", category="goods", size="60g",
                                        material="tube", notes="", fabric=None, colors=[("No.04", png.read_bytes(), "a.png")])
    ecom = await svc.create_plan(db_session, user, data=None, filename="", name="", platform="", style="", count=8,
                                 notes="", category="goods", frame="taobao_34", product_id=row.id, scene_key="fest_chunjie")
    styled = (ecom.plan or {})["styled"]
    pool = [e["key"] for e in styled["elements"]]
    assert pool[:6] == festivals.pack_keys(festivals.BY_KEY["chunjie"])[:6]
    assert "春节" in ecom.notes and "不要编造折扣" in seen["extra"] and "HAPPY NEW YEAR" in seen["extra"]
