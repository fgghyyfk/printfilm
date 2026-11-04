"""每镜视觉资产上限：角色/道具封顶，避免一次超过参考图上限。"""

from types import SimpleNamespace

from app.services.drama.fragment_asset_limit import (
    FRAGMENT_MAX_CHARACTERS,
    FRAGMENT_MAX_VISUAL_ASSETS,
    cap_fragment_asset_ids,
    order_fragment_asset_ids,
    strip_unlisted_asset_mentions,
)
from app.services.media_ref_limits import MAX_REFERENCE_IMAGES, cap_url_list, subject_image_budget
from app.services.drama.fragment_plan import normalize_llm_fragment_items
from app.services.drama.build_fragments import build_fragments_from_episode_body


def _asset(aid: int, kind: str, name: str) -> SimpleNamespace:
    """测试用资产桩。"""
    return SimpleNamespace(id=aid, type=kind, name=name, params={})


def test_cap_fragment_asset_ids_keeps_scene_three_chars_and_props():
    assets = [
        _asset(1, "scene", "大殿"),
        _asset(11, "character", "禹"),
        _asset(12, "character", "舜"),
        _asset(13, "character", "鲧"),
        _asset(14, "character", "四岳"),
        _asset(21, "prop", "开山斧"),
        _asset(22, "prop", "定海针"),
        _asset(23, "prop", "息壤"),
    ]
    ids = [1, 11, 12, 13, 14, 21, 22, 23]
    picked = cap_fragment_asset_ids(ids, assets)
    assert 1 in picked
    assert picked.count(1) == 1
    chars = [i for i in picked if i in {11, 12, 13, 14}]
    props = [i for i in picked if i in {21, 22, 23}]
    assert len(chars) == FRAGMENT_MAX_CHARACTERS
    assert 14 not in picked
    assert len(props) == 2
    assert 23 not in picked
    assert len(picked) <= FRAGMENT_MAX_VISUAL_ASSETS


def test_reference_priority_changes_actual_selected_characters_without_losing_members():
    """混合引用按显式优先级真正换入第四角色，默认旧顺序不变、外部ID不生效。"""
    assets = [_asset(1, "scene", "场景"), *[_asset(i, "character", str(i)) for i in range(11, 15)],
              _asset(21, "prop", "道具一"), _asset(22, "prop", "道具二")]
    ids = [1, 11, 12, 13, 14, 21, 22]
    assert cap_fragment_asset_ids(order_fragment_asset_ids(ids, {}), assets) == [1, 11, 12, 13, 21, 22]
    ordered = order_fragment_asset_ids(ids, {"reference_asset_order": [999, 1, 14, 12, 13, 11, 21, 22, 14]})
    assert set(ordered) == set(ids)
    assert cap_fragment_asset_ids(ordered, assets) == [1, 14, 12, 13, 21, 22]


def test_cap_url_list_and_subject_budget():
    urls = [f"https://cdn.example.com/{i}.png" for i in range(12)]
    assert len(cap_url_list(urls)) == MAX_REFERENCE_IMAGES
    assert subject_image_budget(has_style_board=True) == 8
    assert subject_image_budget(has_style_board=True, has_continuity=True) == 7


def test_strip_unlisted_asset_mentions():
    text = "全景 @asset:1 大殿，@asset:11 禹持 @asset:99 多余道具。"
    assert "@asset:99" not in strip_unlisted_asset_mentions(text, [1, 11])
    assert "@asset:1" in strip_unlisted_asset_mentions(text, [1, 11])


def test_normalize_llm_caps_character_names_to_three():
    assets = [
        _asset(1, "scene", "朝堂"),
        _asset(11, "character", "禹"),
        _asset(12, "character", "舜"),
        _asset(13, "character", "鲧"),
        _asset(14, "character", "四岳"),
        _asset(15, "character", "共工"),
        _asset(21, "prop", "开山斧"),
        _asset(22, "prop", "定海针"),
        _asset(23, "prop", "息壤"),
    ]
    items = [
        {
            "duration_sec": 12,
            "scene_name": "朝堂",
            "character_names": ["禹", "舜", "鲧", "四岳", "共工"],
            "prop_names": ["开山斧", "定海针", "息壤"],
            "lines": [
                "全景：禹、舜、鲧、四岳、共工立于朝堂，开山斧、定海针、息壤陈列案前。",
            ],
        },
    ]
    drafts = normalize_llm_fragment_items(items, assets)
    assert len(drafts) == 1
    ids = drafts[0]["asset_ids"]
    names = drafts[0]["character_names"]
    assert len(names) <= FRAGMENT_MAX_CHARACTERS
    assert 14 not in ids and 15 not in ids
    assert 23 not in ids
    assert len(ids) <= FRAGMENT_MAX_VISUAL_ASSETS
    assert "@asset:14" not in drafts[0]["content"]
    assert "@asset:23" not in drafts[0]["content"]


def test_build_fragments_does_not_attach_whole_scene_cast():
    assets = [
        _asset(1, "scene", "朝堂"),
        _asset(11, "character", "禹"),
        _asset(12, "character", "舜"),
        _asset(13, "character", "鲧"),
        _asset(14, "character", "四岳"),
        _asset(15, "character", "共工"),
    ]
    content = "\n".join(
        [
            "### 场1-1",
            "日内 朝堂",
            "出场人物：禹、舜、鲧、四岳、共工",
            "禹立于殿心拱手。",
            "舜点头示意禹上前。",
        ]
    )
    drafts = build_fragments_from_episode_body(content, assets)
    assert drafts
    ids = drafts[0]["asset_ids"]
    names = drafts[0]["character_names"]
    assert len(names) <= FRAGMENT_MAX_CHARACTERS
    assert len(ids) <= FRAGMENT_MAX_VISUAL_ASSETS
    assert 14 not in ids and 15 not in ids


def test_build_fragments_keeps_later_scene_character_not_in_header_prefix():
    """整场名单很长时，后场只点名的角色仍应挂上，而不是被前 3 人截掉。"""
    assets = [
        _asset(1, "scene", "朝堂"),
        _asset(11, "character", "禹"),
        _asset(12, "character", "舜"),
        _asset(13, "character", "鲧"),
        _asset(14, "character", "四岳"),
        _asset(15, "character", "共工"),
    ]
    content = "\n".join(
        [
            "### 场1-1",
            "日内 朝堂",
            "出场人物：禹、舜、鲧、四岳、共工",
            "禹立于殿心拱手。",
            "### 场1-2",
            "日内 朝堂",
            "出场人物：禹、舜、鲧、四岳、共工",
            "四岳出列禀报水情。",
        ]
    )
    drafts = build_fragments_from_episode_body(content, assets)
    later = next(d for d in drafts if "四岳" in d["content"] or "@asset:14" in d["content"])
    assert 14 in later["asset_ids"]
    assert "四岳" in later["character_names"]
    assert len(later["character_names"]) <= FRAGMENT_MAX_CHARACTERS
    assert len(later["asset_ids"]) <= FRAGMENT_MAX_VISUAL_ASSETS
