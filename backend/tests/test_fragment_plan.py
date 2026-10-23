"""LLM 分镜草稿规范化。"""

from types import SimpleNamespace

from app.services.drama.fragment_plan import normalize_llm_fragment_items
from app.services.drama.fragment_plan_prompt import FRAGMENT_PLAN_SYSTEM_PROMPT


def test_fragment_plan_system_prompt_requires_visual_density():
    # 用户反馈「分镜描述太少」：系统提示必须强制画面公式与结束态，不能只靠 docs
    prompt = FRAGMENT_PLAN_SYSTEM_PROMPT
    assert "画面描写密度" in prompt
    assert "结束态" in prompt
    assert "主体 + 动作" in prompt or "主体 + 动作/姿态" in prompt
    assert "禁止的瘦写法" in prompt
    assert "2–5 行" in prompt or "2-5 行" in prompt


def test_normalize_llm_injects_intro_once_for_important_cast():
    assets = [
        SimpleNamespace(
            id=1,
            type="character",
            name="鲧",
            params={"title": "治水先驱", "roleType": "主角"},
        ),
        SimpleNamespace(
            id=2,
            type="character",
            name="禹",
            params={"title": "治水英雄", "roleType": "主角"},
        ),
        SimpleNamespace(
            id=3,
            type="character",
            name="行刑兵",
            params={"title": "出场人物", "roleType": "配角"},
        ),
        SimpleNamespace(id=10, type="scene", name="羽山刑场", params={}),
    ]
    items = [
        {
            "duration_sec": 10,
            "scene_name": "羽山刑场",
            "character_names": [],
            "is_opening": True,
            "lines": [
                "羽山刑场全景，洪水拍崖。",
                "旁白（VO）：上古洪荒，治水未成。",
            ],
        },
        {
            "duration_sec": 12,
            "scene_name": "羽山刑场",
            "character_names": ["鲧", "禹"],
            "lines": [
                "鲧被绑在杉木柱上。",
                "禹冲上刑场高喊父亲。",
            ],
        },
    ]
    drafts = normalize_llm_fragment_items(
        items,
        assets,
        episode_number=1,
        episode_name="羽山刑场",
        project_title="大禹治水",
        story_type="古风神话",
        one_line_story="鲧禹父子在洪荒中对抗天命。",
    )
    assert len(drafts) == 2
    opening = drafts[0]["content"]
    assert "【片头·集号叠字】第1集｜羽山刑场" in opening
    assert "【片头·剧名叠字】大禹治水" in opening
    assert "【背景介绍·画面叠字】" in opening
    assert "【人物介绍·画面叠字·角色身旁】鲧" not in opening

    joined = "\n---\n".join(d["content"] for d in drafts)
    assert "【人物介绍·画面叠字·角色身旁】鲧｜治水先驱" in joined
    assert "【人物介绍·画面叠字·角色身旁】禹｜治水英雄" in joined
    assert joined.count("【人物介绍·画面叠字·角色身旁】鲧｜") == 1
    assert "【人物介绍·画面叠字·角色身旁】行刑兵" not in joined


def test_normalize_llm_opening_cues_only_on_first_fragment():
    assets = [
        SimpleNamespace(id=1, type="character", name="苏轼", params={"title": "才子", "roleType": "主角"}),
        SimpleNamespace(id=10, type="scene", name="汴京街市", params={}),
    ]
    opening_block = [
        "【BGM：轻柔开阔，希望感】",
        "【片头·集号叠字】第1集｜乌龙伯乐压奇才",
        "【片头·词条特写】史上最难高考",
        "【片头·标语特写】两风历史",
        "空镜：汴京晨雾中的学堂门匾。",
    ]
    items = [
        {
            "duration_sec": 10,
            "scene_name": "汴京街市",
            "character_names": [],
            "is_opening": True,
            "lines": opening_block,
        },
        {
            "duration_sec": 12,
            "scene_name": "汴京街市",
            "character_names": ["苏轼"],
            "lines": [
                *opening_block,
                "苏轼：大宋嘉祐二年。",
            ],
        },
    ]
    drafts = normalize_llm_fragment_items(
        items,
        assets,
        episode_number=1,
        episode_name="乌龙伯乐压奇才",
        project_title="乌龙伯乐压奇才",
    )
    assert len(drafts) >= 2
    assert "【片头·集号叠字】" in drafts[0]["content"]
    assert "【片头·词条特写】" in drafts[0]["content"]
    for draft in drafts[1:]:
        assert "【片头" not in draft["content"]
        assert "苏轼" in draft["content"]


def test_normalize_llm_links_prop_and_material():
    assets = [
        SimpleNamespace(id=1, type="character", name="禹", params={"title": "治水", "roleType": "主角"}),
        SimpleNamespace(id=10, type="scene", name="裂石崖", params={}),
        SimpleNamespace(id=20, type="prop", name="开山斧", params={}),
        SimpleNamespace(id=21, type="material", name="定海针", params={}),
    ]
    items = [
        {
            "duration_sec": 12,
            "scene_name": "裂石崖",
            "character_names": ["禹"],
            "prop_names": ["开山斧"],
            "material_names": ["定海针"],
            "lines": [
                "全景：禹右手握开山斧，左手持定海针立于裂石崖边。",
            ],
        },
    ]
    drafts = normalize_llm_fragment_items(items, assets)
    assert len(drafts) == 1
    content = drafts[0]["content"]
    ids = drafts[0]["asset_ids"]
    assert 20 in ids and 21 in ids and 1 in ids and 10 in ids
    assert "@asset:20" in content
    assert "@asset:21" in content


def test_build_asset_catalog_excludes_material_and_voice():
    from app.services.drama.fragment_plan import build_asset_catalog

    assets = [
        SimpleNamespace(id=1, type="character", name="禹", params={}),
        SimpleNamespace(id=2, type="prop", name="开山斧", params={}),
        SimpleNamespace(id=3, type="material", name="定海针", params={}),
        SimpleNamespace(id=4, type="voice", name="旁白", params={}),
    ]
    catalog = build_asset_catalog(assets)
    kinds = {c["type"] for c in catalog}
    assert "character" in kinds and "prop" in kinds
    assert "material" not in kinds
    assert "voice" not in kinds


def test_normalize_llm_splits_when_durations_exceed_hard_max():
    # LLM 把过多对白塞进一镜时，规范化须按 15s 硬上限拆成多条
    import re

    long = "这是一句足够长的对白用来推高单行估算时长到上限附近，确保多行合计必然超过三十秒硬上限。"
    assets = [
        SimpleNamespace(id=1, type="character", name="禹", params={"title": "治水", "roleType": "主角"}),
        SimpleNamespace(id=10, type="scene", name="工地", params={}),
    ]
    items = [
        {
            "duration_sec": 30,
            "scene_name": "工地",
            "character_names": ["禹"],
            "lines": [
                f"禹：{long}",
                f"禹：{long}",
                f"禹：{long}",
                f"禹：{long}",
                f"禹：{long}",
            ],
        },
    ]
    drafts = normalize_llm_fragment_items(items, assets)
    assert len(drafts) >= 2
    for draft in drafts:
        total = sum(int(m) for m in re.findall(r"@duration:(\d+)", draft["content"]))
        assert total <= 15
        assert draft["duration_sec"] == total


def test_llm_short_duration_cannot_squeeze_long_dialogue():
    import re

    # 99 spoken characters need at least 33 seconds at the documented slow pace.
    speech = "藏" * 99
    drafts = normalize_llm_fragment_items(
        [{"duration_sec": 4, "lines": [f"朵朵：{speech}"]}],
        [],
        allow_opening=False,
    )
    spoken_parts = []
    durations = []
    for draft in drafts:
        assert draft["duration_sec"] <= 15
        durations.extend(int(value) for value in re.findall(r"@duration:(\d+)", draft["content"]))
        spoken_parts.extend(re.findall(r"朵朵：([^\n]+)", draft["content"]))
    assert "".join(spoken_parts) == speech
    assert sum(durations) >= 33
    assert all(3 <= duration <= 12 for duration in durations)
    assert len(drafts) >= 3


def test_short_llm_duration_keeps_dialogue_and_visual_time():
    drafts = normalize_llm_fragment_items(
        [{"duration_sec": 4, "lines": ["近景：朵朵捧起一颗松果。", "朵朵：现在藏好松果，冬天就不会饿肚子了。"]}],
        [],
        allow_opening=False,
    )
    assert sum(draft["duration_sec"] for draft in drafts) >= 8
    joined = "\n".join(draft["content"] for draft in drafts)
    assert "朵朵捧起一颗松果" in joined
    assert "现在藏好松果，冬天就不会饿肚子了。" in joined


def test_long_narration_preserves_clauses_when_split_into_timed_beats():
    import re

    speech = "秋天到了，朵朵每天认真收集松果，把松果藏在不同的树下。冬天来了，它终于明白提前准备的重要，也愿意把找到的松果分给森林里的朋友。"
    drafts = normalize_llm_fragment_items(
        [{"duration_sec": 5, "lines": [f"旁白（VO）：{speech}"]}],
        [],
        allow_opening=False,
    )
    pieces = [
        piece
        for draft in drafts
        for piece in re.findall(r"旁白（VO）：([^\n]+)", draft["content"])
    ]
    assert "".join(pieces) == speech
    assert len(pieces) >= 2
    assert sum(draft["duration_sec"] for draft in drafts) >= 19


def test_asset_tokens_are_not_counted_as_spoken_markup():
    assets = [SimpleNamespace(id=123456, type="character", name="朵朵", params={})]
    drafts = normalize_llm_fragment_items(
        [{"duration_sec": 4, "character_names": ["朵朵"], "lines": ["朵朵：朵朵最喜欢松果了。"]}],
        assets,
        allow_opening=False,
    )
    assert len(drafts) == 1
    assert drafts[0]["duration_sec"] == 3
    assert "@asset:123456" in drafts[0]["content"]


def test_visual_shot_label_is_not_timed_as_a_speaking_character():
    drafts = normalize_llm_fragment_items(
        [{"duration_sec": 4, "lines": ["空镜：森林中松树层层叠叠，阳光穿过针叶照亮泥土，风吹叶动。"]}],
        [],
        allow_opening=False,
    )
    assert len(drafts) == 1
    assert drafts[0]["duration_sec"] <= 4
    assert "【画面·无配音仅环境音】空镜：" in drafts[0]["content"]


def test_locked_fragment_summary_keeps_tagged_action_and_dialogue():
    """续拆要知道已拍情节，不能把所有带【前缀的内容过滤掉。"""
    from app.services.drama.fragment_dialogue import summarize_locked_fragment

    content = (
        "【BGM：钢琴】\n【字幕：关闭】\n【片头·集号叠字】第1集\n"
        "@duration:5\n【画面·无配音仅环境音】近景：@asset:7埋好种子。\n"
        "@duration:6\n【对白·慢速清晰】@asset:7：冬天就有粮食吃了。"
    )
    assets = [SimpleNamespace(id=7, name="朵朵")]
    summary = summarize_locked_fragment(content, assets)
    assert "朵朵埋好种子" in summary
    assert "朵朵：冬天就有粮食吃了" in summary
    assert "@asset" not in summary
    assert "@duration" not in summary
    assert "BGM" not in summary
    assert "第1集" not in summary
