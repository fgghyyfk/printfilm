"""角色 seed 的类别纠错：纯函数回归，不连接数据库或调用模型。"""

from copy import deepcopy

import pytest

from app.services.drama.seed import collect_episode_seed_names
from app.services.drama.summary_character_filter import partition_summary_characters


def test_rejects_explicit_environment_and_non_anthropomorphic_seed_objects():
    summary = {
        "characters": [
            {"name": "朵朵", "roleType": "主角", "visualImage": "幼年小松鼠"},
            {"name": "蹦蹦", "roleType": "配角", "visualImage": "幼年灰兔"},
            {
                "name": "松果们",
                "roleType": "特殊群体角色",
                "visualImage": "非拟人化角色，以CGI精细呈现：成熟松果棕褐鳞片层叠。",
                "identityBackground": "森林原生植物种子，承担食物与繁殖双重功能",
            },
            {
                "name": "森林",
                "roleType": "场景/氛围角色",
                "visualImage": "非人物，以环境拟态呈现：秋季树冠如金红云团。",
                "identityBackground": "温带针阔混交林生态系统，无意志但有规律",
            },
        ]
    }
    original = deepcopy(summary)

    accepted, excluded = partition_summary_characters(summary)

    assert list(accepted) == ["朵朵", "蹦蹦"]
    assert excluded == {"松果们", "森林"}
    assert summary == original


@pytest.mark.parametrize(
    "character",
    [
        {"name": "村民们", "roleType": "特殊群体角色", "visualImage": "一群村民"},
        {"name": "树妖", "roleType": "场景/氛围角色", "identityBackground": "有自主意识的古树"},
        {"name": "小铁", "roleType": "道具", "visualImage": "拟人化机器人，能够和主人对话"},
        {"name": "松果精灵", "roleType": "特殊群体角色", "identityBackground": "由植物种子化成的精灵"},
        {"name": "朵朵", "roleType": "主角", "visualImage": "非拟人化角色，以真实松鼠形态行动"},
        {
            "name": "朵朵",
            "roleType": "主角",
            "visualImage": "非拟人化角色，一只叼着植物种子的松鼠",
            "identityBackground": "在林间寻找种子的小松鼠",
        },
        {"name": "森林", "roleType": "配角", "title": "森林守护者", "visualImage": "一名年轻女孩"},
        {"name": "道具师", "roleType": "道具", "identityBackground": "负责舞台道具的工作人员"},
        {"name": "木匣", "roleType": "配角", "identityBackground": "普通木制道具"},
    ],
)
def test_keeps_real_characters_and_ambiguous_entries(character):
    accepted, excluded = partition_summary_characters({"characters": [character]})
    assert accepted == {character["name"]: character}
    assert excluded == set()


@pytest.mark.parametrize(
    "role_type, visual, identity",
    [
        ("道具", "普通木制匣子", "无生命器物"),
        ("非拟人化道具", "普通木制匣子", "存放信件的道具"),
        ("场景", "无人居住的旧宅", "一座建筑"),
        ("氛围角色", "非人物，四季变化的环境", ""),
        ("非人物", "无生命的石头", ""),
    ],
)
def test_rejects_clearly_declared_non_character_assets(role_type, visual, identity):
    accepted, excluded = partition_summary_characters(
        {"characters": [{"name": "测试项", "roleType": role_type, "visualImage": visual, "identityBackground": identity}]}
    )
    assert accepted == {}
    assert excluded == {"测试项"}


def test_invalid_entries_and_valid_duplicate_do_not_block_real_cast():
    real = {"name": "森林", "roleType": "主角", "visualImage": "一名人类探险家"}
    summary = {
        "characters": [
            None,
            "bad",
            {},
            {"name": "森林", "roleType": "场景", "visualImage": "非人物，生态环境"},
            real,
        ]
    }
    accepted, excluded = partition_summary_characters(summary)
    assert accepted == {"森林": real}
    assert excluded == set()


def test_cast_cannot_reintroduce_summary_entries_identified_as_non_characters():
    summary = {
        "characters": [
            {"name": "朵朵", "roleType": "主角"},
            {"name": "森林", "roleType": "场景/氛围角色", "visualImage": "非人物，生态环境"},
            {
                "name": "松果们",
                "roleType": "特殊群体角色",
                "visualImage": "非拟人化角色，成熟的松果",
                "identityBackground": "森林原生植物种子",
            },
        ]
    }
    body = "### 场1-1\n日 外 林间\n出场人物：朵朵、森林、松果们、蹦蹦\n△ 朵朵埋下松果。"

    characters, scenes, by_name = collect_episode_seed_names(summary, body)

    assert characters == ["朵朵", "蹦蹦"]
    assert scenes == ["林间"]
    assert list(by_name) == ["朵朵"]


def test_undeclared_cast_names_still_seed_normally():
    names, _, _ = collect_episode_seed_names(
        {"characters": []},
        "### 场1-1\n日 外 林间\n出场人物：树妖、机器人、村民们\n△ 大家相遇。",
    )
    assert names == ["树妖", "机器人", "村民们"]
