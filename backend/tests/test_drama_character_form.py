"""非人角色（会说话的纸鹤、动物等）走非人设定板，不被人物模板画成人。"""

from __future__ import annotations

import pytest

from app.services.drama.character_form import is_non_human_character
from app.services.drama.generation_prompt import (
    CHARACTER_PROMPT_PREFIX,
    NON_HUMAN_CHARACTER_PROMPT_PREFIX,
    build_generation_prompt,
    ensure_structure_in_prompt,
)
from app.services.drama.seed_asset_params import build_character_params
from app.services.seedream_text_soften import (
    compact_seedream_prompt_for_retry,
    split_seedream_structure_prompt,
    style_only_seedream_prompt_for_retry,
)

CRANE = "一只由泛黄信纸折成的纸鹤，约巴掌大小，纸面带着岁月痕迹，在雨中会发出淡金色的微光。"


@pytest.mark.parametrize("text,expected", [
    (CRANE, True),
    ("一头银灰色的老狼，左耳缺了一角", True),
    ("由竹篾编成的灯笼精灵，会发光", True),
    ("十六七岁的少女，圆润的脸庞", False),
    ("一位白发老人，拄着拐杖", False),
    ("一只橘猫少女，头顶猫耳", False),
    ("一只狐狸，修炼千年后化为人形", False),
    ("盛唐顶流，白衣佩剑", False),
    ("", False),
])
def test_is_non_human_character(text, expected):
    assert is_non_human_character(text) is expected


def test_seeded_crane_uses_non_human_board():
    params = build_character_params({"name": "纸鹤", "visualImage": CRANE, "title": "百年前的旧信化身"})
    for key in ("visualImage", "visualPrompt"):
        assert params[key].startswith(NON_HUMAN_CHARACTER_PROMPT_PREFIX)
    human = build_character_params({"name": "阿青", "visualImage": "十六七岁的少女，黑色短发齐耳"})
    assert human["visualPrompt"].startswith(CHARACTER_PROMPT_PREFIX)


def test_old_human_prefix_is_fixed_for_crane_only():
    """旧资产已落库人物前缀：刷新与提交时换成非人前缀，正文原样保留。"""
    old = f"{CHARACTER_PROMPT_PREFIX}{CRANE}"
    fixed = ensure_structure_in_prompt(old, "character")
    assert fixed == f"{NON_HUMAN_CHARACTER_PROMPT_PREFIX}{CRANE}"
    submitted = build_generation_prompt(old, "character", "ghibli-handdrawn-anime")
    assert submitted.startswith(NON_HUMAN_CHARACTER_PROMPT_PREFIX) and "不得据此画出人物" in submitted
    girl = f"{CHARACTER_PROMPT_PREFIX}十六七岁的少女"
    assert ensure_structure_in_prompt(girl, "character") == girl
    assert "不得据此画出人物" not in build_generation_prompt(girl, "character", "ghibli-handdrawn-anime")
    # 道具不受影响
    assert ensure_structure_in_prompt(CRANE, "prop").startswith("【强制任务：道具设定板构图】")


def test_retry_prompts_keep_non_human():
    full = ensure_structure_in_prompt(CRANE, "character")
    assert split_seedream_structure_prompt(full)[1] == CRANE
    compact = compact_seedream_prompt_for_retry(full)
    assert "非人角色设定板" in compact and "纸鹤" in compact
    style_only = style_only_seedream_prompt_for_retry(full)
    assert "非人角色设定板" in style_only and "书生" not in style_only
    human = compact_seedream_prompt_for_retry(ensure_structure_in_prompt("十六七岁的少女", "character"))
    assert "非人" not in human
