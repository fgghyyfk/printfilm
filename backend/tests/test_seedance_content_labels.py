"""describe_seedance_content_slots 与 content[] 下标对齐。"""

from app.services.drama.build_seedance_generate_body import (
    build_seedance_content_items,
    describe_seedance_content_slots,
)


def test_content_slot_labels_match_content_indices():
    reference = [
        {
            "id": 1,
            "type": "scene",
            "name": "羽山",
            "cover": "https://example.com/scene.jpg",
            "url": "",
            "params": {},
        },
        {
            "id": 2,
            "type": "character",
            "name": "相柳",
            "cover": "https://example.com/a.jpg",
            "url": "",
            "params": {},
        },
        {
            "id": 3,
            "type": "character",
            "name": "流民群演",
            "cover": "https://example.com/b.jpg",
            "url": "",
            "params": {},
        },
    ]
    items = build_seedance_content_items(
        "文案",
        reference,
        continuity_first_frame_url=None,
    )
    labels = describe_seedance_content_slots(reference, None, has_text=True)
    assert len(labels) == len(items)
    assert labels[0] == "分镜文案"
    assert "羽山" in labels[1]
    assert "相柳" in labels[2]
    assert "流民群演" in labels[3]


def test_style_board_sits_after_assets_before_continuity():
    reference = [
        {
            "id": 1,
            "type": "character",
            "name": "相柳",
            "cover": "https://example.com/a.jpg",
            "url": "",
            "params": {},
        }
    ]
    board = "https://cdn.example.com/ghibli-board.png"
    continuity = "https://cdn.example.com/prev_last.jpg"
    items = build_seedance_content_items(
        "文案",
        reference,
        video_style_id="ghibli-handdrawn-anime",
        continuity_first_frame_url=continuity,
        style_board_url=board,
    )
    labels = describe_seedance_content_slots(
        reference,
        continuity,
        has_text=True,
        style_board_url=board,
    )
    assert len(labels) == len(items)
    urls = [
        item["image_url"]["url"]
        for item in items
        if item.get("type") == "image_url"
    ]
    assert urls == ["https://example.com/a.jpg", board, continuity]
    assert labels == ["分镜文案", "角色「相柳」", "画风板", "上一镜尾帧"]
    assert "只借鉴其色调、笔触、光影" in items[0]["text"]
    assert all(item.get("role") == "reference_image" for item in items if item.get("type") == "image_url")


def test_voice_asset_cover_mp3_not_in_image_slots():
    """音色 cover 写成 mp3 时不得进 reference_image（否则上游 content[n] invalid image format）。"""
    reference = [
        {
            "id": 1,
            "type": "character",
            "name": "甲",
            "cover": "https://cdn.example.com/a.jpg",
            "url": "",
            "params": {"voiceAudio": {"url": "https://cdn.example.com/voice.mp3"}},
        },
        {
            "id": 2,
            "type": "scene",
            "name": "室",
            "cover": "https://cdn.example.com/s.jpg",
            "url": "",
            "params": {},
        },
        {
            "id": 3,
            "type": "voice",
            "assetType": "audio",
            "name": "试听",
            "cover": "https://cdn.example.com/voice.mp3",
            "url": "https://cdn.example.com/voice.mp3",
            "params": {},
        },
    ]
    script = "甲：你好。"
    items = build_seedance_content_items(script, reference)
    labels = describe_seedance_content_slots(
        reference, None, has_text=True, content=script
    )
    image_urls = [
        item["image_url"]["url"]
        for item in items
        if item.get("type") == "image_url"
    ]
    audio_urls = [
        item["audio_url"]["url"]
        for item in items
        if item.get("type") == "audio_url"
    ]
    assert image_urls == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/s.jpg",
    ]
    assert "voice.mp3" not in "".join(image_urls)
    assert "https://cdn.example.com/voice.mp3" in audio_urls
    assert len(labels) == len(items)
    assert labels[3].startswith("角色音色") or "音色" in labels[3]


def test_character_cover_mp3_rejected_as_image():
    """角色 cover 误写成音频时也不进 reference_image。"""
    from app.services.drama.build_seedance_generate_body import resolve_reference_image_url

    assert (
        resolve_reference_image_url(
            {
                "id": 9,
                "type": "character",
                "cover": "https://cdn.example.com/bad.mp3",
                "url": "",
            }
        )
        is None
    )


def test_style_board_ignored_without_catalog_images():
    continuity = "https://cdn.example.com/prev_last.jpg"
    board = "https://cdn.example.com/ghibli-board.png"
    items = build_seedance_content_items(
        "文案",
        [],
        continuity_first_frame_url=continuity,
        style_board_url=board,
    )
    labels = describe_seedance_content_slots(
        [],
        continuity,
        has_text=True,
        style_board_url=board,
    )
    urls = [
        item["image_url"]["url"]
        for item in items
        if item.get("type") == "image_url"
    ]
    assert urls == [continuity]
    assert items[-1].get("role") == "first_frame"
    assert labels == ["分镜文案", "上一镜尾帧"]
    assert "画风板" not in labels
