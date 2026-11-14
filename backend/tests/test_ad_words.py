"""广告法极限词库：拦截替换、提醒、白名单、正则与自定义词。"""

from __future__ import annotations

from app.services.compliance import ad_words
from app.services.compliance.ad_words import BLOCK, WARN, check_text, summarize


# 各类明确违规词被替换或删除
def test_blocks_each_category():
    cases = {
        "国家级品质": "国家级",
        "行业第一品牌": "第一",
        "顶级面料": "顶级",
        "仅此一次大促": "仅此一次",
        "根治失眠": "根治",
        "开光转运手链": "开光",
    }
    for text, word in cases.items():
        clean, hits = check_text(text)
        assert word not in clean, text
        assert any(word in h.word and h.level == BLOCK for h in hits), text


# 长词优先，替换后语句通顺
def test_long_words_first():
    assert check_text("全网最好用的暖壶")[0] == "好用的暖壶"
    assert check_text("销量第一")[0] == "热销"


# 「最」兜底与白名单
def test_most_fallback_and_whitelist():
    assert check_text("最轻薄的设计")[0] == "轻薄的设计"
    clean, hits = check_text("最近上新，能效一级，最后一天")
    assert clean == "最近上新，能效一级，最后一天"
    assert not [h for h in hits if h.level == BLOCK]


# 英文与百分比绝对化
def test_regex_rules():
    clean, hits = check_text("NO.1 保温壶 TOP1 好评 1000%")
    assert "NO.1" not in clean and "TOP1" not in clean and "1000%" not in clean
    assert len([h for h in hits if h.level == BLOCK]) >= 3


# 提醒档保留原文
def test_warn_level_keeps_text():
    clean, hits = check_text("99% 抑菌 真丝面料")
    assert "抑菌" in clean and "真丝" in clean
    assert {h.word for h in hits if h.level == WARN} >= {"抑菌", "真丝"}


# 汇总提示语
def test_summarize():
    _, hits = check_text("顶级抑菌")
    msgs = summarize(hits)
    assert any("顶级" in m for m in msgs) and any("抑菌" in m for m in msgs)


# 配置里追加的自定义禁用词生效
def test_extra_words(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ad_banned_extra", "爆款神器，闭眼入")
    ad_words.reload_words()
    try:
        clean, hits = check_text("爆款神器 闭眼入")
        assert clean == ""
        assert {h.category for h in hits} == {"自定义"}
    finally:
        monkeypatch.setattr(get_settings(), "ad_banned_extra", "")
        ad_words.reload_words()


# 清洗后不留首尾标点
def test_trims_punctuation():
    assert check_text("绝对，好用")[0] == "好用"


# 白名单：科普/剧情常见正常用语不被误改
def test_whitelist_common_content_words():
    for text in ("第一次见到它", "历史上最早的记录", "第一集开场", "永久冻土层"):
        clean, _ = check_text(text)
        assert clean.startswith(text[:3]), (text, clean)


# 报告模式只记录不改字
def test_report_mode_keeps_text():
    text = "国家级自然保护区里最大的湖"
    clean, hits = check_text(text, mode=ad_words.REPORT)
    assert clean == text
    assert any(h.word == "国家级" for h in hits)
    assert any("未修改" in m for m in summarize(hits, mode=ad_words.REPORT))


# 多行脚本保留换行与行首标记
def test_multiline_preserved():
    text = "@duration:5\n【旁白·自然语速】这是全网最好用的方法\n  角色（vo）：顶级"
    clean, _ = check_text(text)
    lines = clean.split("\n")
    assert len(lines) == 3 and lines[0] == "@duration:5"
    assert lines[1].startswith("【旁白·自然语速】") and "最好" not in lines[1]
    assert lines[2].startswith("  角色（vo）：")


# 化妆品功效宣称（需功效评价报告）：屏障修护、抗敏、零刺激等替换为不含功效承诺的说法；普通「修复」不误伤
def test_cosmetic_efficacy_claims():
    from app.services.compliance.ad_words import check_text

    out, hits = check_text("苦参根提取物：增强肌肤屏障，亲和人体皮脂")
    assert out == "苦参根提取物：呵护肌肤，亲和人体皮脂" and hits[0].category == "化妆品功效"
    assert check_text("零刺激配方，抗敏舒缓")[0] == "温和配方，舒缓"
    assert check_text("头发修复精华")[1] == []
    assert check_text("温和远离刺激")[1] == []
