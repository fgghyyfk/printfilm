"""参考音色时长：下限与补足、提交前校验。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.drama.voice_reference_audio import (
    SEEDANCE_REFERENCE_AUDIO_MAX_SEC,
    SEEDANCE_REFERENCE_AUDIO_MIN_SEC,
    VOICE_REFERENCE_MIN_SEC,
    VOICE_REFERENCE_TARGET_SEC,
    is_voice_duration_too_long,
    is_voice_duration_too_short,
    patch_params_voice_url,
    validate_seedance_body_reference_audios,
)
from app.services.drama.voice_synthesis import build_voice_sample_line_short


def test_voice_reference_min_meets_seedance_api():
    assert VOICE_REFERENCE_MIN_SEC >= SEEDANCE_REFERENCE_AUDIO_MIN_SEC
    assert VOICE_REFERENCE_TARGET_SEC < SEEDANCE_REFERENCE_AUDIO_MAX_SEC
    assert SEEDANCE_REFERENCE_AUDIO_MAX_SEC <= 30.2


def test_is_voice_duration_too_short():
    assert is_voice_duration_too_short(1.2) is True
    assert is_voice_duration_too_short(1.79) is True
    assert is_voice_duration_too_short(2.0) is False
    assert is_voice_duration_too_short(None) is False


def test_is_voice_duration_too_long():
    assert is_voice_duration_too_long(30.0) is False
    assert is_voice_duration_too_long(30.5) is True
    assert is_voice_duration_too_long(None) is False


def test_sample_line_long_enough_for_tts():
    line = build_voice_sample_line_short("双龙")
    # 中文 TTS 约 3–4 字/秒；两句应明显长于 1.8s
    assert len(line) >= 20
    assert "双龙" in line


def test_patch_params_voice_url():
    params = {
        "voiceAudio": {"sourceAssetId": 1, "url": "/static/old.mp3", "label": "禹音色"},
        "canvas": {"voiceAudio": {"sourceAssetId": 1, "url": "/static/old.mp3"}},
    }
    next_params = patch_params_voice_url(params, "/static/new.mp3")
    assert next_params["voiceAudio"]["url"] == "/static/new.mp3"
    assert next_params["canvas"]["voiceAudio"]["url"] == "/static/new.mp3"


def _seedance_body_with_audio(url: str = "https://cdn.example.com/v.mp3") -> dict:
    return {
        "content": [
            {"type": "text", "text": "分镜"},
            {
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            },
        ]
    }


@pytest.mark.asyncio
async def test_validate_reference_audio_rejects_too_short():
    body = _seedance_body_with_audio()
    labels = ["分镜文案", "角色音色「禹」"]
    with patch(
        "app.services.drama.voice_reference_audio.probe_voice_url_duration",
        new=AsyncMock(return_value=1.2),
    ):
        with pytest.raises(RuntimeError, match="参考音频过短") as exc:
            await validate_seedance_body_reference_audios(
                body, content_labels=labels, project_id=1
            )
    assert "禹" in str(exc.value)
    assert "1.2" in str(exc.value)


@pytest.mark.asyncio
async def test_validate_reference_audio_rejects_too_long():
    body = _seedance_body_with_audio()
    with patch(
        "app.services.drama.voice_reference_audio.probe_voice_url_duration",
        new=AsyncMock(return_value=31.0),
    ):
        with pytest.raises(RuntimeError, match="参考音频过长"):
            await validate_seedance_body_reference_audios(body, project_id=1)


@pytest.mark.asyncio
async def test_validate_reference_audio_rejects_unreadable():
    body = _seedance_body_with_audio()
    with patch(
        "app.services.drama.voice_reference_audio.probe_voice_url_duration",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(RuntimeError, match="无法读取参考音频时长"):
            await validate_seedance_body_reference_audios(body, project_id=1)


@pytest.mark.asyncio
async def test_validate_reference_audio_accepts_valid():
    body = _seedance_body_with_audio()
    with patch(
        "app.services.drama.voice_reference_audio.probe_voice_url_duration",
        new=AsyncMock(return_value=22.0),
    ):
        await validate_seedance_body_reference_audios(body, project_id=1)
