"""LLM 分镜过少（长正文/多场只 1 镜）应判定不足并回退。"""

from types import SimpleNamespace

from app.services.drama.fragment_plan import llm_fragment_coverage_insufficient


def test_single_draft_ok_for_short_one_scene():
    body = "### 场1-1\n日内 厨房\n出场人物：甲\n甲推门进来。"
    drafts = [{"content": "@duration:8\n画面", "duration_sec": 8}]
    assert llm_fragment_coverage_insufficient(body, drafts) is False


def test_single_draft_insufficient_for_multi_scene():
    body = "\n".join(
        [
            "### 场2-1",
            "日内 厨房",
            "出场人物：甲、乙",
            "甲与乙对峙。",
            "### 场2-2",
            "夜外 巷口",
            "出场人物：甲",
            "甲独自离开。",
        ]
    )
    drafts = [{"content": "@duration:15\n开幕", "duration_sec": 15}]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_single_draft_insufficient_for_long_body():
    body = "旁白：" + ("这是很长的剧情叙述用于累计字数。" * 40)
    drafts = [{"content": "@duration:15\n一镜", "duration_sec": 15}]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_two_drafts_cover_two_scenes():
    body = "### 场1-1\n日内 A\n甲进。\n### 场1-2\n夜外 B\n乙走。"
    drafts = [
        {"content": "@duration:10\na", "duration_sec": 10},
        {"content": "@duration:10\nb", "duration_sec": 10},
    ]
    assert llm_fragment_coverage_insufficient(body, drafts) is False


def test_two_drafts_insufficient_for_three_scenes():
    body = "\n".join(
        [
            "### 场3-1",
            "日内 A",
            "出场人物：甲",
            "甲进门。",
            "### 场3-2",
            "夜外 B",
            "出场人物：乙",
            "乙离开。",
            "### 场3-3",
            "日外 C",
            "出场人物：甲、乙",
            "两人重逢。",
        ]
    )
    drafts = [
        {"content": "@duration:10\na", "duration_sec": 10},
        {"content": "@duration:10\nb", "duration_sec": 10},
    ]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_many_drafts_never_capped_as_insufficient():
    """镜数多不算不足；不设上限。"""
    body = "### 场1-1\n日内 A\n### 场1-2\n夜外 B\n" + ("旁白。" * 80)
    drafts = [{"content": f"@duration:8\n镜{i}", "duration_sec": 8} for i in range(12)]
    assert llm_fragment_coverage_insufficient(body, drafts) is False


def test_enough_unrelated_drafts_cannot_replace_source_dialogue():
    body = "\n".join(
        [
            "### 场1-1\n日外 林地\n出场人物：朵朵、蹦蹦",
            "朵朵：现在把松果藏好，冬天就不会饿肚子。",
            "### 场1-2\n夜外 雪林\n出场人物：朵朵",
            "朵朵：我记得松果就藏在这棵树下。",
            "### 场1-3\n日外 松林\n出场人物：蹦蹦",
            "蹦蹦：你藏的松果都变成新松树啦！",
        ]
    )
    drafts = [{"content": "空镜：云朵飘过天空。", "duration_sec": 8} for _ in range(6)]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_dialogue_coverage_restores_assets_and_ignores_emotion_and_punctuation():
    body = "朵朵（开心）：蹦蹦，你看！我藏的松果变成小树了。"
    drafts = [
        {
            "content": (
                "@duration:5\n【对白·慢速清晰·同步字幕】@asset:1：@asset:2你看，\n"
                "@duration:7\n【对白·慢速清晰·同步字幕】@asset:1：我藏的松果变成小树了！"
            ),
            "duration_sec": 12,
        }
    ]
    assets = [SimpleNamespace(id=1, name="朵朵"), SimpleNamespace(id=2, name="蹦蹦")]
    assert llm_fragment_coverage_insufficient(body, drafts, assets=assets) is False


def test_dialogue_in_visual_description_does_not_count_as_spoken_coverage():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。"
    drafts = [{"content": f"【画面·无配音仅环境音】特写：{body}", "duration_sec": 8}]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_dialogue_assigned_to_wrong_character_is_incomplete():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。"
    drafts = [{"content": "蹦蹦：现在把松果藏好，冬天就不会饿肚子。", "duration_sec": 8}]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_missing_one_main_line_is_incomplete_even_when_other_dialogue_survives():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。\n朵朵：这些被忘记的松果，原来都长成新松树了。"
    drafts = [
        {"content": "朵朵：现在把松果藏好，冬天就不会饿肚子。", "duration_sec": 8},
        {"content": "空镜：金色阳光洒进森林。", "duration_sec": 6},
    ]
    assert llm_fragment_coverage_insufficient(body, drafts) is True


def test_visual_only_story_can_be_reframed_without_dialogue_false_positive():
    body = "### 场1-1\n日外 林地\n出场人物：朵朵\n△ 松鼠埋好松果，压平泥土。"
    drafts = [{"content": "【画面·无配音仅环境音】特写：小爪把泥土轻轻拍实。", "duration_sec": 6}]
    assert llm_fragment_coverage_insufficient(body, drafts) is False


def test_legacy_unknown_assets_do_not_trigger_automatic_dialogue_replan():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。"
    drafts = [{"content": "【对白·慢速清晰】@asset:42：现在把松果藏好，冬天就不会饿肚子。"}]
    assert llm_fragment_coverage_insufficient(body, drafts) is False


def test_unknown_reference_cannot_bypass_check_when_catalog_is_available():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。"
    drafts = [{"content": "【对白·慢速清晰】@asset:42：这是一段与原剧本无关的话。"}]
    assert llm_fragment_coverage_insufficient(body, drafts, assets=[]) is True


def test_continuation_does_not_need_to_repeat_locked_dialogue():
    body = "朵朵：现在把松果藏好，冬天就不会饿肚子。\n蹦蹦：那我们现在就去森林里收集松果吧。"
    drafts = [{"content": "蹦蹦：那我们现在就去森林里收集松果吧。", "duration_sec": 10}]
    assert llm_fragment_coverage_insufficient(body, drafts, check_dialogue=False) is False
