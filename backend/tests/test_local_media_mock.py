"""Media simulation must never intercept real text requests or call media providers."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services import local_media_mock
from app.services.ark import ArkGateway


@pytest.mark.asyncio
async def test_media_returns_playable_fixtures_and_records_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(local_media_mock, "LOG_PATH", tmp_path / "requests.jsonl")
    network = AsyncMock(side_effect=AssertionError("Media provider must not be contacted"))
    monkeypatch.setattr("app.services.ark.observed_request", network)
    gateway = ArkGateway(Settings(local_media_mock=True, ark_mock=False, ark_base_url="https://www.tokenfree.com/v1"))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr("app.services.seedance_image_aspect.ensure_seedance_compatible_image_url", AsyncMock(side_effect=lambda url: url))
    monkeypatch.setattr(gateway, "_resolve_image_ref", AsyncMock(side_effect=lambda url, **kwargs: url))
    assert gateway.mock is False
    image = await gateway.gen_image("测试图片", ref_urls=["https://example.com/ref?token=secret"])
    task_id = await gateway.gen_video_i2v("https://example.com/ref.jpg", "测试运镜", 5, prompt_as_json=False)
    other_id = await gateway.gen_video_seedance_body({"model": "seedance-2.5", "content": [{"type": "text", "text": "测试多模态"}]})
    audio = await gateway.tts("测试配音", "narrator_calm")
    for ident in (task_id, other_id, "old-provider-id"):
        result = await gateway.fetch_task_once(ident)
        assert result.status == "succeeded"
        assert result.url == "/static/mock/local/default.mp4"
        assert result.raw_usage["simulated"] is True
    assert (await gateway.poll_task(task_id)).url.endswith("default.mp4")
    assert image.local_url.endswith("default.jpg")
    assert audio.endswith("default.mp3")
    network.assert_not_called()
    records = [json.loads(line) for line in local_media_mock.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert [r["operation"] for r in records] == ["image.generate", "video.generate", "video.generate", "audio.tts"]
    assert all(r["simulated"] for r in records)
    assert all(r["method"] == "POST" and r["endpoint"].startswith("https://") and r["request_stage"] == "http_ready" for r in records)
    assert "secret" not in local_media_mock.LOG_PATH.read_text(encoding="utf-8")
    assert records[1]["request"]["body"]["prompt"] == "测试运镜"
    # Files must contain actual encoded media, not missing paths or JSON placeholders.
    assert (local_media_mock.ASSET_ROOT / "default.mp4").read_bytes()[4:8] == b"ftyp"
    assert (local_media_mock.ASSET_ROOT / "default.jpg").read_bytes()[:2] == b"\xff\xd8"


@pytest.mark.asyncio
async def test_text_still_uses_real_client_path_when_media_mock_enabled(monkeypatch):
    from app.services import llm_client
    settings = Settings(local_media_mock=True, ark_mock=False, openai_api_key="test", model_llm="text-test")
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "resolve_logical_model", lambda *args: None)
    monkeypatch.setattr(llm_client, "resolve_logical_model_id", lambda *args: "text-test")
    request = AsyncMock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": "真实文字响应"}}]}))
    monkeypatch.setattr(llm_client, "observed_request", request)
    assert await llm_client.chat_completions("system", "user") == "真实文字响应"
    assert request.await_args.args[1] == "POST"
    assert request.await_args.args[2].endswith("/chat/completions")
    assert request.await_args.kwargs["json"]["messages"][1]["content"] == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_json_requested", [False, True])
async def test_minimax_receives_full_plain_prompt_including_newlines(monkeypatch, tmp_path, legacy_json_requested):
    from types import SimpleNamespace
    monkeypatch.setattr(local_media_mock, "LOG_PATH", tmp_path / "requests.jsonl")
    gateway = ArkGateway(Settings(local_media_mock=True, ark_mock=False))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: SimpleNamespace(
        upstream_model="MiniMax-H3", base_url="https://www.tokenfree.com/v1", channel_id="tokenfree", api_key="test-key"))
    monkeypatch.setattr("app.services.seedance_image_aspect.ensure_seedance_compatible_image_url", AsyncMock(side_effect=lambda url: url))
    monkeypatch.setattr(gateway, "_resolve_image_ref", AsyncMock(side_effect=lambda url, **kwargs: url))
    prompt = "镜头缓慢推进，保留完整对白。\n第二段有空格  和换行。\n" * 45
    await gateway.gen_video_i2v("https://example.test/ref.jpg", prompt, 15, ratio="9:16", prompt_as_json=legacy_json_requested)
    record = json.loads(local_media_mock.LOG_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert record["request"]["body"]["prompt"] == prompt.strip()
    assert len(record["request"]["body"]["prompt"]) > 500
    assert "summary_caption" not in record["request"]["body"]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["video_i2v", "video_multimodal", "image", "speech", "chat_audio"])
async def test_mock_captures_same_final_http_arguments_as_real_gateway(monkeypatch, tmp_path, kind):
    from app.services.upstream_sanitize import sanitize_upstream_value
    from types import SimpleNamespace
    monkeypatch.setattr(local_media_mock, "LOG_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr("app.services.seedance_image_aspect.ensure_seedance_compatible_image_url", AsyncMock(side_effect=lambda url: url))
    monkeypatch.setattr("app.services.drama.voice_reference_audio.validate_seedance_body_reference_audios", AsyncMock())
    gateways = [ArkGateway(Settings(local_media_mock=value, ark_mock=False, ark_api_key="test-key")) for value in [False, True]]
    for gateway in gateways:
        monkeypatch.setattr(gateway, "_resolve_ark_route", lambda capability, *args: SimpleNamespace(
            upstream_model="MiniMax-H3" if capability == "video" else "gpt-image-2.5",
            base_url="https://www.tokenfree.com/v1", channel_id="tokenfree", api_key="test-key"))
        monkeypatch.setattr(gateway, "_resolve_image_ref", AsyncMock(side_effect=lambda url, **kwargs: url))
    captured = []
    class CapturedAtTransport(RuntimeError):
        pass
    async def capture(client, method, endpoint, **kwargs):
        captured.append((method, endpoint, kwargs))
        raise CapturedAtTransport("captured before network")
    monkeypatch.setattr("app.services.ark.observed_request", capture)
    async def invoke(gateway):
        if kind == "video_i2v":
            return await gateway.gen_video_i2v("https://example.test/ref.jpg", "camera movement", 15, ratio="9:16", generate_audio=True)
        if kind == "video_multimodal":
            return await gateway.gen_video_seedance_body({"model": "MiniMax-H3", "duration": 15, "ratio": "9:16", "resolution": "480p", "content": [{"type": "text", "text": "camera movement"}, {"type": "image_url", "image_url": {"url": "https://example.test/ref.jpg"}, "role": "reference_image"}]})
        if kind == "image":
            return await gateway.gen_image("image prompt", model="gpt-image-2.5", aspect_ratio="9:16", size="2K")
        if kind == "speech":
            return await gateway._tts_openai_speech("hello", "alloy", tmp_path / "speech.mp3", model="tts-1")
        return await gateway._tts_openai_chat_audio("hello", "alloy", tmp_path / "chat.mp3", model="gemini-2.5-flash-preview-tts")
    with pytest.raises(CapturedAtTransport):
        await invoke(gateways[0])
    await invoke(gateways[1])
    assert len(captured) == 1, "simulation must not invoke real transport"
    method, endpoint, kwargs = captured[0]
    record = json.loads(local_media_mock.LOG_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert record["method"] == method
    assert record["endpoint"] == endpoint
    assert record["request"] == sanitize_upstream_value({"headers": kwargs["headers"], "body": kwargs["json"]})
    if kind.startswith("video"):
        assert endpoint.endswith("/videos")
        assert "seconds" in record["request"]["body"]
        assert "metadata" in record["request"]["body"]


