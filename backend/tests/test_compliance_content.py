"""开口播文案合规：按模板选模式、镜头字段清洗（只动旁白行）、科普/漫剧扫描。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.compliance.ad_words import REPLACE, REPORT
from app.services.compliance.content import (
    drama_spoken_text,
    is_ad_template,
    mode_for_template,
    sanitize_script,
    sanitize_shot_fields,
    scan_fragments,
    scan_shots,
)


# 获客/商业模板走替换，科普走提示
def test_mode_by_template():
    huoke = SimpleNamespace(category=["科普", "获客", "商业"], seedream_config={})
    kepu = SimpleNamespace(category=["科普"], seedream_config={})
    named = SimpleNamespace(category=["科普"], seedream_config={"allow_source_names": True})
    assert is_ad_template(huoke) and mode_for_template(huoke) == REPLACE
    assert not is_ad_template(kepu) and mode_for_template(kepu) == REPORT
    assert is_ad_template(named)
    assert mode_for_template(None) == REPORT
    # 只标「商业」的风格模板不算带货
    assert mode_for_template(SimpleNamespace(category=["商业", "极简"], seedream_config={})) == REPORT


# 脚本只清洗旁白行，画面/字幕/BGM/时长行原样保留
def test_sanitize_script_only_narration_lines():
    script = (
        "@duration:5\n"
        "画面：顶级厨房里的保温壶特写\n"
        "【旁白·自然语速·同步字幕】这是全网最好用的保温壶\n"
        "【BGM】顶级轻快"
    )
    out, hits = sanitize_script(script, mode=REPLACE)
    lines = out.split("\n")
    assert lines[0] == "@duration:5"
    assert lines[1] == "画面：顶级厨房里的保温壶特写"
    assert lines[2].startswith("【旁白·自然语速·同步字幕】") and "最好" not in lines[2]
    assert lines[3] == "【BGM】顶级轻快"
    assert hits


# 镜头字段：替换后重点词要仍是旁白子串
def test_shot_fields_highlights_stay_substrings():
    fields, hits = sanitize_shot_fields(
        {"narration": "顶级保温，一按出水", "overlay_title": "全网第一", "highlights": "顶级保温|一按出水"},
        mode=REPLACE,
    )
    assert "顶级" not in fields["narration"]
    assert fields["highlights"] == "一按出水"
    assert "第一" not in fields["overlay_title"]
    assert hits


# 提示模式不改任何字段
def test_report_mode_changes_nothing():
    src = {"narration": "国家级保护区里最大的湖", "overlay_title": "第一次见"}
    fields, hits = sanitize_shot_fields(src, mode=REPORT)
    assert fields == src
    assert any(h.word == "国家级" for h in hits)


# 科普扫描：按镜头/字段返回命中
def test_scan_shots():
    shots = [
        SimpleNamespace(id=11, shot_no=1, narration="正常讲解", overlay_title="", overlay_subtitle=""),
        SimpleNamespace(id=12, shot_no=2, narration="这是顶级方案", overlay_title="", overlay_subtitle="抑菌"),
    ]
    items = scan_shots(shots, title="史无前例的发现")
    scopes = [(i["scope"], i["field"]) for i in items]
    assert ("project", "title") in scopes
    assert ("shot", "narration") in scopes and ("shot", "overlay_subtitle") in scopes
    assert all(i["shot_id"] in (None, 12) for i in items)


# 漫剧：只看台词，不看结构行与说话人名
def test_drama_spoken_text_and_scan():
    content = "@duration:6\n### 场1-1\n出场人物：冠军、小美\n冠军（vo）：这是全网第一的秘方\n小美：好的"
    spoken = drama_spoken_text(content)
    assert "@duration" not in spoken and "出场人物" not in spoken
    assert spoken.split("\n")[0] == "这是全网第一的秘方"
    items = scan_fragments([SimpleNamespace(id=5, sort_order=1, content=content)])
    words = {h["word"] for h in items[0]["hits"]}
    assert "冠军" not in words and ("全网第一" in words or "第一" in words)
