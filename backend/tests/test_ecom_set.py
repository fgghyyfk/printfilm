"""一图出套图：预设、文案过滤、规划解析与对齐、排版渲染、计费估价（纯单测，不需要数据库）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.models_tasks import TaskRun
from app.services.ecom_set import layout, planner, presets, prompts


# 张数规整与套图构成
def test_counts_and_composition():
    assert presets.normalize_count("8") == 8
    assert presets.normalize_count(5) == presets.DEFAULT_COUNT
    assert presets.normalize_count(None) == presets.DEFAULT_COUNT
    for n in presets.COUNTS:
        slots = presets.slots_for_count(n)
        assert len(slots) == n
        assert slots[0].key == "main_white"


# 平台尺寸与生图画幅一致（小红书主图 3:4 不能按 1:1 生成）
def test_platform_sizes_and_ratio():
    assert presets.output_size("taobao", "main_white") == (800, 800)
    assert presets.output_size("taobao", "detail") == (750, 1000)
    assert presets.gen_ratio("taobao", "main_scene") == "1:1"
    assert presets.gen_ratio("xhs", "main_scene") == "3:4"
    assert presets.normalize_platform("unknown") == presets.DEFAULT_PLATFORM


# 极限词替换并报告命中
def test_sanitize_copy_replaces_banned_words():
    text, hits = prompts.sanitize_copy("全网最好用的第一保温壶")
    words = [h.word for h in hits]
    assert "最好用" in words and "第一" in words and "全网" in words
    assert "最" not in text and "第一" not in text and "全网" not in text


# 文案截断、无文案版式清空
def test_normalize_copy_limits():
    copy, _ = prompts.normalize_copy(
        {"title": "一" * 30, "subtitle": "二" * 30, "bullets": ["三" * 20, "b", "c", "d"]},
        layout="title_bullets",
    )
    assert len(copy["title"]) == prompts.TITLE_MAX
    assert len(copy["subtitle"]) == prompts.SUBTITLE_MAX
    assert len(copy["bullets"]) == prompts.BULLETS_MAX
    assert len(copy["bullets"][0]) == prompts.BULLET_MAX
    empty, _ = prompts.normalize_copy({"title": "x"}, layout="none")
    assert empty == {"title": "", "subtitle": "", "bullets": []}


# 底图提示词包含锁定商品外观与留白，且画面描述里的极限词和「要写上的字」被清掉
def test_scene_prompt_locks_product():
    slot = presets.SLOTS["selling_1"]
    p = prompts.build_scene_prompt(slot=slot, scene="顶级厨房台面，墙上写着全网第一", style="warm", blank="top")
    assert "厨房台面" in p and "完全一致" in p and "留白" in p
    assert "顶级" not in p and "第一" not in p and "全网" not in p


# 兼容 ```json 包裹与前后废话
def test_extract_json_variants():
    assert planner.extract_json('好的\n```json\n{"a": 1}\n```')["a"] == 1
    assert planner.extract_json('结果：{"a": {"b": 2}} 完')["a"]["b"] == 2
    with pytest.raises(ValueError):
        planner.extract_json("没有 json")


# 模型少返回/乱序时按槽位对齐，缺文案用品名兜底
def test_align_items_fills_missing():
    slots = presets.slots_for_count(4)
    raw = [{"slot": "usage", "scene": "客厅", "title": "随手可用"}, {"slot": "main_white", "title": "应被清空"}]
    items, _ = planner.align_items(raw, slots, name="保温壶")
    assert [i["slot"] for i in items] == [s.key for s in slots]
    assert items[0]["copy"]["title"] == ""          # 白底主图无文案
    assert items[3]["scene"] == "客厅"               # 按 slot 匹配
    assert items[1]["copy"]["title"] == "保温壶"     # 缺标题用品名


# mock 规划可直接对齐
def test_mock_plan_aligns():
    slots = presets.slots_for_count(6)
    data = planner.mock_plan(slots, name="测试商品")
    items, _ = planner.align_items(data["items"], slots, name="测试商品")
    assert len(items) == 6


# 各版式都能渲染出目标尺寸的 JPEG（长文案不报错）
@pytest.mark.parametrize("lay,blank", [
    ("none", "none"), ("title_corner", "top"), ("title_sub", "top"),
    ("title_bullets", "top"), ("label_bar", "bottom"),
])
def test_render_layouts(tmp_path: Path, lay: str, blank: str):
    try:
        layout.font_path(True)
    except RuntimeError:
        pytest.skip("本机没有中文字体")
    product = tmp_path / "p.jpg"
    Image.new("RGB", (600, 900), (30, 60, 140)).save(product)
    base = layout.mock_base(product, tmp_path / "base.jpg", ratio="3:4", seed="x")
    out = layout.render(
        base, tmp_path / f"{lay}.jpg", size=(750, 1000), layout=lay, blank=blank,
        copy={"title": "超长的标题文字会自动缩小换行", "subtitle": "副标题" * 6, "bullets": ["真空保温", "大容量", "防漏"]},
    )
    with Image.open(out) as im:
        assert im.size == (750, 1000)
        assert im.format == "JPEG"


# 折行不超过最大宽度
def test_wrap_respects_width():
    try:
        font = layout.load_font(40, True)
    except RuntimeError:
        pytest.skip("本机没有中文字体")
    lines = layout.wrap("一二三四五六七八九十" * 3, font, 200, 2)
    assert len(lines) <= 2
    assert all(font.getlength(line) <= 200 for line in lines)
    assert lines[-1].endswith("…")


# 计费估价：规划按配置固定价，单张按生图张价
async def test_estimates(monkeypatch):
    from app.config import get_settings
    from app.services.billing.estimates import estimate_task_fen

    s = get_settings()
    monkeypatch.setattr(s, "ecom_set_plan_fen", 10)
    plan = await estimate_task_fen(None, TaskRun(domain="studio", task_type="ecom_set_plan", payload={}))
    assert plan == 10
    img = await estimate_task_fen(None, TaskRun(domain="studio", task_type="ecom_set_image", payload={}))
    assert img >= 1


# 任务平台已登记两个任务类型
def test_handlers_registered():
    from app.services.tasks.handlers import get_task_handler

    assert get_task_handler("studio", "ecom_set_plan") is not None
    assert get_task_handler("studio", "ecom_set_image") is not None


# 回归：第 0 张的 idx=0 不能被当成缺失（曾导致第一张永远排队）
def test_parse_target_keeps_zero_idx():
    from app.services.ecom_set.jobs import parse_target

    assert parse_target({"set_id": 3, "idx": 0}) == (3, 0)
    assert parse_target({"set_id": 3, "idx": 2}) == (3, 2)
    assert parse_target({"set_id": 3}) == (3, -1)


# 「最好用」要替换成通顺的「好用」，不能变成「优选用」
def test_sanitize_keeps_sentence_fluent():
    text, _ = prompts.sanitize_copy("全网最好用的暖壶")
    assert text == "好用的暖壶"


# 副标题软截断：略超几个字原样保留，超太多在标点处断开，不把词截成半个
def test_subtitle_soft_clip():
    assert prompts.clip_soft("20V锂电配2.0Ah电池无线作业", 16) == "20V锂电配2.0Ah电池无线作业"
    long = "两档调速，多档扭力调节，正反转一键切换，家具组装墙面打孔都适用"
    assert prompts.clip_soft(long, 16) == "两档调速，多档扭力调节"
    assert prompts.clip_soft("二" * 30, 16) == "二" * 16
