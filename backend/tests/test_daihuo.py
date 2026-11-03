"""商品带货：口播解析对齐、兜底、极限词替换、画幅补边、模板分类。"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.services.compliance.content import is_ad_template
from app.services.daihuo import frames, script
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATES

ITEMS = [
    {"idx": 0, "slot_label": "主图·白底", "scene": "白底", "title": "", "subtitle": "", "bullets": []},
    {"idx": 1, "slot_label": "核心卖点", "scene": "厨房", "title": "大容量", "subtitle": "全家够用", "bullets": ["2.5L"]},
    {"idx": 3, "slot_label": "使用场景", "scene": "餐桌", "title": "一按出水", "subtitle": "", "bullets": []},
]


# 模型输出按 idx 对齐，缺段用兜底，极限词被替换
def test_parse_segments_aligns_and_sanitizes():
    raw = "```json\n" + json.dumps({
        "title": "全网最好用的暖壶",
        "segments": [
            {"idx": 3, "title": "顶级出水", "narration": "轻轻一按热水就出来了，不用再弯腰倒水。"},
            {"idx": 0, "title": "看这里", "narration": "还在为倒热水烫手发愁吗？这款暖壶帮你解决。"},
        ],
    }, ensure_ascii=False) + "\n```"
    out = script.parse_segments(raw, ITEMS, name="气压保温壶")
    assert [s["idx"] for s in out["segments"]] == [0, 1, 3]
    assert "最好" not in out["title"] and "全网" not in out["title"]
    assert "顶级" not in out["segments"][2]["title"]
    assert out["segments"][1]["narration"]  # 缺段兜底
    assert all(len(s["narration"]) <= script.SEG_MAX_CHARS for s in out["segments"])


# 完全解析失败时全部兜底，首段钩子、末段号召
def test_parse_segments_garbage_falls_back():
    out = script.parse_segments("不是 json", ITEMS, name="保温壶")
    segs = out["segments"]
    assert len(segs) == 3
    assert "保温壶" in segs[0]["narration"] and "链接" in segs[-1]["narration"]
    assert out["title"] == "保温壶"


# 写稿提示包含每张图的用途与文案
def test_build_user_prompt():
    p = script.build_user_prompt(name="保温壶", notes="24 小时保温", product={"selling_points": ["按压出水"]}, items=ITEMS)
    assert "按压出水" in p and "24 小时保温" in p and "核心卖点" in p and "大容量/全家够用/2.5L" in p


# 方图补成 9:16，尺寸正确且主体不被裁
def test_pad_to_frame(tmp_path: Path):
    src = tmp_path / "s.jpg"
    Image.new("RGB", (800, 800), (200, 30, 30)).save(src)
    out = frames.pad_to_frame(src, tmp_path / "f.jpg", ratio="9:16")
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
        r, g, b = im.getpixel((540, 960))
        assert r > 150 and g < 80  # 中心是原图主体颜色
    assert frames.frame_size("bad") == (1080, 1920)


# 带货模板走极限词替换模式
def test_daihuo_template_is_ad():
    tpl = DAIHUO_TEMPLATES[0]
    from types import SimpleNamespace

    assert is_ad_template(SimpleNamespace(category=tpl["category"], seedream_config=tpl["seedream_config"]))
    assert tpl["default_ratio"] == "9:16"


# 超长标题退回套图标题，不硬截断成半句
def test_fit_title():
    assert script.fit_title("2.5L大容量 24小时保温", "大容量长效保温") == "大容量长效保温"
    assert script.fit_title("一按出水", "别的") == "一按出水"
    assert script.fit_title("", "") == ""
