"""提交 Seedance 前：名字不叠写、角色 VO 不进旁白、未开口不挂音色。"""

from app.services.drama.build_seedance_generate_body import (
    build_seedance_content_items,
    build_seedance_prompt_text,
    describe_seedance_content_slots,
    normalize_canvas_freeform_speech,
)


def test_submitted_prompt_keeps_visual_style_and_body_without_other_forced_sections():
    from app.services.tokenfree_video import prepare_video_create_body
    reference = [_char(1, "角色甲", audio="https://cdn.example.com/voice.mp3")]
    script = "@duration:6\n【画面·无配音仅环境音】@asset:1 走到窗前。\n【对白·慢速清晰】角色甲：我们出发。"
    items = build_seedance_content_items(script, reference, video_style_id="korean-urban-soft",
        style_board_url="https://cdn.example.com/style.png", continuity_first_frame_url="https://cdn.example.com/last.jpg")
    body = prepare_video_create_body({"model": "MiniMax-H3", "content": items, "duration": 6, "ratio": "9:16"})
    prompt = body["prompt"]
    assert "【强制约束：视频画面风格】" in prompt
    assert "韩剧" in prompt
    assert "另附画风参考图" in prompt
    assert prompt.count("【强制约束：") == 1
    assert "走到窗前" in prompt and "我们出发" in prompt
    assert "00:00-00:06" in prompt
    media = body["metadata"]["content"]
    assert any(item.get("image_url", {}).get("url") == "https://cdn.example.com/style.png" for item in media)
    assert any(item.get("image_url", {}).get("url") == "https://cdn.example.com/last.jpg" for item in media)
    assert any(item.get("audio_url", {}).get("url") == "https://cdn.example.com/voice.mp3" for item in media)
from app.services.seedance_segments import (
    build_seedance_production_section,
    rewrite_misclassified_visual_voice_lines,
)


def _char(asset_id: int, name: str, *, audio: str | None = None) -> dict:
    params = {}
    if audio:
        params["voiceAudio"] = {"url": audio}
    return {
        "id": asset_id,
        "type": "character",
        "assetType": "image",
        "name": name,
        "cover": f"https://cdn.example.com/{name}.png",
        "url": "",
        "params": {**params, "entityName": name},
    }


def _scene(asset_id: int, name: str) -> dict:
    return {
        "id": asset_id,
        "type": "scene",
        "assetType": "image",
        "name": name,
        "cover": f"https://cdn.example.com/{name}.png",
        "url": "",
        "params": {"entityName": name},
    }


def _prop(asset_id: int, name: str) -> dict:
    return {
        "id": asset_id,
        "type": "prop",
        "assetType": "image",
        "name": name,
        "cover": f"https://cdn.example.com/{name}.png",
        "url": "",
        "params": {"entityName": name},
    }


# 对齐生产事故：第一镜正文已写名字又挂 @asset
_ACI_SCRIPT = """@duration:7 【配乐】｜木吉他轻快起步，渐弱
【场景】日 · 外 · 现代校门口街道（夕阳）@asset:4876
【画面·全景】放学人潮中，阿词@asset:4859 和小胖@asset:4860 并肩慢慢走。小胖抱着贴满贴纸的搬家纸箱@asset:4882 ，两人一路无话
【旁白】阿词（vo，低落）。
【旁白】阿词："明天，我最好的朋友小胖，就要搬去上海了。"
"""

_ACI_REFERENCE = [
    _scene(4876, "校门口"),
    _char(4859, "阿词", audio="https://cdn.example.com/aci.mp3"),
    _char(4860, "小胖", audio="https://cdn.example.com/pang.mp3"),
    _prop(4882, "搬家纸箱"),
]


def test_asset_mention_does_not_repeat_adjacent_name():
    prompt = build_seedance_prompt_text(_ACI_SCRIPT, _ACI_REFERENCE, burn_subtitles=False)
    assert "阿词阿词" not in prompt
    assert "小胖小胖" not in prompt
    assert "搬家纸箱搬家纸箱" not in prompt
    # 阿词有旁白开口 → 挂 @音频；小胖未开口不挂音色
    assert "阿词（@图片2/参考图2，@音频1/参考音频1）" in prompt
    assert "小胖（@图片3/参考图3）" in prompt
    assert "搬家纸箱（@图片4/参考图4）" in prompt
    assert "校门口（@图片1/参考图1）" in prompt


def test_asset_mention_strips_name_after_token():
    script = "【画面】@asset:4876 校门口。"
    prompt = build_seedance_prompt_text(script, [_scene(4876, "校门口")], burn_subtitles=False)
    assert "校门口（@图片1/参考图1） 校门口" not in prompt
    assert "校门口校门口" not in prompt
    assert "校门口（@图片1/参考图1）" in prompt


def test_short_asset_name_does_not_eat_longer_name():
    script = "【画面】大禹@asset:1 站在河边。"
    prompt = build_seedance_prompt_text(script, [_char(1, "禹")], burn_subtitles=False)
    assert "大（参考图1）" not in prompt
    assert "大禹" in prompt


def test_os_stage_stays_inner_monologue():
    assert "内心独白" in rewrite_misclassified_visual_voice_lines("小宇（OS）：原来月亮是这样变化的。")
    assert "内心独白" in rewrite_misclassified_visual_voice_lines("【旁白】阿词（os，低落）。")
    closeup = rewrite_misclassified_visual_voice_lines("阿词（close-up）：站住。")
    assert "内心独白" not in closeup
    assert "【对白" in closeup


def test_labeled_speaker_attaches_reference_audio():
    """开口角色的试听会挂进 content 作 reference_audio。"""
    script = "@duration:6\n禹：水患未平。"
    reference = [_char(1, "禹", audio="https://cdn.example.com/yu.mp3")]
    items = build_seedance_content_items(script, reference, burn_subtitles=False)
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert audios == ["https://cdn.example.com/yu.mp3"]
    assert "【对白" in items[0]["text"]
    assert "【强制约束：角色音色】" not in items[0]["text"]


def test_canvas_freeform_say_attaches_reference_audio():
    """画布「说 …」自由提示词也应挂已绑定音色，不能因无「角色名：」而漏传。"""
    prompt = (
        "新角色 站在肯德基门店入口前，近景，张嘴说话。"
        "说 疯狂星期四 v我50 请你吃脆皮炸鸡"
    )
    reference = [_char(3897, "新角色", audio="https://cdn.example.com/male.mp3")]
    items = build_seedance_content_items(prompt, reference, burn_subtitles=False)
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert audios == ["https://cdn.example.com/male.mp3"]
    assert "【强制约束：角色音色】" not in items[0]["text"]


def test_brace_dialogue_splits_and_strips_visual_brace_noise():
    """说{台词} 须拆成【对白】；画面行里说{中景…} 噪声须删除，避免 {} 抢口播。"""
    char = _char(50, "大禹", audio="https://cdn.example.com/yu.mp3")
    scene = {
        "id": 7299,
        "type": "image",
        "assetType": "image",
        "name": "新图片",
        "cover": "https://cdn.example.com/kfc.png",
        "url": "",
        "params": {},
    }
    # 真台词花括号 → 对白 + 挂音色
    speech = (
        "中景，固定镜头，@asset:50 站在 @asset:7299 门口。"
        "他嘴角微微上扬。说{我请你吃疯狂星期四}"
    )
    prompt = build_seedance_prompt_text(speech, [char, scene], burn_subtitles=False)
    assert "【对白·慢速清晰】" in prompt
    assert "我请你吃疯狂星期四" in prompt
    assert "说{" not in prompt.split("【对白")[0]
    assert "【强制约束：角色音色】" not in prompt
    assert "@音频1" in prompt

    # 画面描述误入花括号 → 只删噪声，不升格对白
    noisy = (
        "【画面·无配音仅环境音】中景，@asset:50 站在 @asset:7299 门口，"
        "他嘴角微微上扬 。 说{7299 门口，中景，正面朝向镜头，背景是肯德基红色招牌与玻璃门}\n"
        "【对白·慢速清晰】@asset:50：我请你吃疯狂星期四"
    )
    fixed = normalize_canvas_freeform_speech(noisy, [char, scene])
    assert "说{" not in fixed
    assert "{7299" not in fixed
    assert "【对白·慢速清晰】@asset:50：我请你吃疯狂星期四" in fixed
    assert "门口" in fixed


def test_character_vo_narration_becomes_dialogue_not_stage_direction():
    fixed = rewrite_misclassified_visual_voice_lines(_ACI_SCRIPT)
    assert "【旁白】阿词（vo，低落）" not in fixed
    assert "【画面" in fixed and "阿词（vo，低落）" in fixed
    assert "【对白" in fixed
    assert "明天，我最好的朋友小胖" in fixed
    assert "【旁白】阿词：" not in fixed
    section = build_seedance_production_section(fixed, burn_subtitles=False)
    assert "第三人称旁白" not in section
    assert "【对白" in section or "对白" in section


def test_true_third_person_narration_stays_narration():
    script = "@duration:4\n【画面】校门口\n【旁白】明天，小胖就要搬走了。"
    fixed = rewrite_misclassified_visual_voice_lines(script)
    assert "【旁白】明天，小胖就要搬走了。" in fixed
    assert "【对白" not in fixed


def test_bound_voice_submitted_as_reference_audio():
    """已绑定试听仍提交 reference_audio，不再追加音色强制约束。"""
    items = build_seedance_content_items(_ACI_SCRIPT, _ACI_REFERENCE, burn_subtitles=False)
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert "https://cdn.example.com/aci.mp3" in audios
    text = items[0]["text"]
    assert "【强制约束：角色音色】" not in text
    assert "参考音频" in text
    labels = describe_seedance_content_slots(
        _ACI_REFERENCE,
        None,
        has_text=True,
        content=_ACI_SCRIPT,
    )
    assert any("音色" in label for label in labels)


def test_peiyue_cue_feeds_production_bgm():
    section = build_seedance_production_section(_ACI_SCRIPT, burn_subtitles=False)
    assert "木吉他轻快起步，渐弱" in section
    assert "贴合内容的轻量配乐" not in section


def test_prop_reference_is_kept_without_extra_constraint_section():
    prompt = build_seedance_prompt_text(_ACI_SCRIPT, _ACI_REFERENCE, burn_subtitles=False)
    assert "【强制约束：道具】" not in prompt
    assert "@图片4" in prompt
    assert "搬家纸箱" in prompt


def test_canvas_image_references_keep_indices_without_extra_constraints():
    """保留画布图片和正文引用序号，不追加场景或角色强制约束段。"""
    image_ref = {
        "id": 9001,
        "type": "image",
        "assetType": "image",
        "name": "新图片",
        "cover": "https://cdn.example.com/kfc.png",
        "url": "",
        "params": {"entityName": "新图片"},
    }
    char = _char(9002, "妖皇", audio="https://cdn.example.com/yao.mp3")
    script = (
        "@asset:9002 站在 @asset:9001 门口，中景，正面朝向镜头，"
        "背景是肯德基红色招牌与玻璃门。他说：'v我50请你吃疯狂星期四'。"
    )
    prompt = build_seedance_prompt_text(script, [char, image_ref], burn_subtitles=False)
    assert "【强制约束：场景】" not in prompt
    assert "@图片2" in prompt and "新图片" in prompt
    assert "【强制约束：角色形象】" not in prompt
    assert "@图片1" in prompt and "妖皇" in prompt
    assert "新图片（@图片2/参考图2）" in prompt
    # @asset: 冒号不得把整段打成对白；他说："…" 应拆出真正对白行并挂 @音频
    assert "【强制约束：角色音色】" not in prompt
    assert "@音频1" in prompt
    # 不再追加图片N中的角色用音频M说的强制命令。
    assert "@图片1中的角色「妖皇」必须使用@音频1" not in prompt
    assert "【对白·慢速清晰】" in prompt
    assert "妖皇（@图片1/参考图1，@音频1/参考音频1）" in prompt
    assert "v我50请你吃疯狂星期四" in prompt
    assert "中景，正面朝向镜头" in prompt
    # 画面描述不应整段落在对白 cue 里
    dialogue_lines = [ln for ln in prompt.splitlines() if ln.startswith("【对白")]
    assert dialogue_lines
    assert all("中景" not in ln for ln in dialogue_lines)

    items = build_seedance_content_items(script, [char, image_ref], burn_subtitles=False)
    image_urls = [
        item["image_url"]["url"]
        for item in items
        if item.get("type") == "image_url"
    ]
    assert image_urls == [
        "https://cdn.example.com/妖皇.png",
        "https://cdn.example.com/kfc.png",
    ]
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert audios == ["https://cdn.example.com/yao.mp3"]
    labels = describe_seedance_content_slots(
        [char, image_ref], None, has_text=True, content=script
    )
    assert any("场景「新图片」" in label for label in labels)
    assert any("角色音色「妖皇」" in label for label in labels)


def test_string_voice_audio_url_is_attached():
    """OSS/历史数据可能把 voiceAudio 写成纯字符串，仍须挂 reference_audio。"""
    from app.services.drama.build_seedance_generate_body import read_asset_voice_audio_url

    char = {
        "id": 9002,
        "type": "character",
        "assetType": "image",
        "name": "妖皇",
        "cover": "https://cdn.example.com/yao.png",
        "url": "",
        "params": {
            "entityName": "妖皇",
            "voiceAudio": "https://cdn.example.com/yao-str.mp3",
        },
    }
    assert read_asset_voice_audio_url(char["params"]) == "https://cdn.example.com/yao-str.mp3"
    script = "【对白·慢速清晰】妖皇：你好。"
    items = build_seedance_content_items(script, [char], burn_subtitles=False)
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert audios == ["https://cdn.example.com/yao-str.mp3"]
    prompt = build_seedance_prompt_text(script, [char], burn_subtitles=False)
    assert "【强制约束：角色音色】" not in prompt
    assert "你好" in prompt


def test_asset_colon_not_treated_as_speaker_dialogue():
    """@asset:id 中的冒号不能触发「说话人：台词」分类。"""
    from app.services.seedance_segments import classify_voice_body
    from app.services.drama.build_fragments import _format_narrative_line

    assert classify_voice_body("@asset:9002 站在门口，中景") == "keep"
    formatted = _format_narrative_line(
        "@asset:9002 站在 @asset:9001 门口，中景，正面朝向镜头。"
    )
    assert formatted.startswith("【画面·无配音仅环境音】")
    assert "@asset:9002" in formatted


def test_canvas_music_audio_is_bound_as_reference_audio():
    """画布独立音频要进 content，并在正文写成 @音频，否则 Seedance 不用这段配乐。"""
    music = {
        "id": 7881,
        "type": "audio",
        "assetType": "audio",
        "name": "新音频",
        "cover": "",
        "url": "https://cdn.example.com/music.mp3",
        "params": {},
    }
    scene = _scene(7877, "广场")
    script = "【音效】脚步踏土声，音乐参考音频@asset:7881 【台词】无；口型：闭嘴。"
    items = build_seedance_content_items(script, [scene, music], burn_subtitles=False)
    audios = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url" and item.get("role") == "reference_audio"
    ]
    assert audios == ["https://cdn.example.com/music.mp3"]
    prompt = str(items[0]["text"])
    assert "【强制约束：参考音乐】" not in prompt
    assert "@音频1" in prompt
    assert "说出全部对白" not in prompt


def test_hearsay_not_split_as_freeform_dialogue():
    """「据说/听说」不得被拆成【对白】。"""
    from app.services.drama.build_seedance_generate_body import normalize_canvas_freeform_speech

    for line in (
        "据说：'明天要搬家'。",
        "听说：\"他不来了\"。",
        "小说：「开篇第一句」。",
    ):
        out = normalize_canvas_freeform_speech(line, [])
        assert "【对白" not in out
        assert out.strip() == line
