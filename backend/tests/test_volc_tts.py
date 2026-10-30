"""火山引擎豆包配音：音色识别、资源选择、流式响应拼音频、错误码转中文、语气指令只给 2.0 音色。"""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import volc_tts


def _settings(**kw):
    """最小配置。"""
    base = dict(volc_tts_api_key="k", volc_tts_base_url="https://volc.test", volc_tts_resource_id="seed-tts-1.0",
                volc_tts_speech_rate=10, volc_tts_instruction="热情口播", volc_tts_fen_per_10k_chars=500)
    base.update(kw)
    return SimpleNamespace(**base)


def test_speaker_and_resource_rules(monkeypatch):
    """2.0 / ICL 2.0 用 seed-tts-2.0，复刻 S_ 用 seed-icl-2.0，其余按后台默认；OpenAI/微软音色不走火山。"""
    monkeypatch.setattr(volc_tts, "get_settings", lambda: _settings())
    assert volc_tts.is_volc_speaker("zh_female_vv_uranus_bigtts")
    assert volc_tts.is_volc_speaker("ICL_uranus_zh_female_lingdongxinxin_tob")
    assert not volc_tts.is_volc_speaker("openai_shimmer")
    assert volc_tts.resource_for("zh_female_vv_uranus_bigtts") == "seed-tts-2.0"
    assert volc_tts.resource_for("S_abc123") == "seed-icl-2.0"
    assert volc_tts.resource_for("zh_female_cancan_mars_bigtts") == "seed-tts-1.0"


def _mock_transport(lines, seen):
    """按行返回 JSON 的假火山接口，记录请求头与请求体。"""
    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    return httpx.MockTransport(handler)


@pytest.fixture
def patched(monkeypatch):
    """替换配置、HTTP 客户端与记账。"""
    monkeypatch.setattr(volc_tts, "get_settings", lambda: _settings())
    recorded = []

    async def fake_record(*args, **kwargs):
        recorded.append((args, kwargs))

    from app.services import kepu_avatar_billing

    monkeypatch.setattr(kepu_avatar_billing, "record_avatar_ai", fake_record)
    return recorded


def _use_transport(monkeypatch, transport):
    """让 volc_tts 里的 AsyncClient 走假传输。"""
    real = httpx.AsyncClient
    monkeypatch.setattr(volc_tts.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


@pytest.mark.asyncio
async def test_synthesize_joins_chunks_and_bills(monkeypatch, tmp_path, patched):
    """多块 base64 拼成一个 mp3；2.0 音色带语气指令；按字记账。"""
    audio = b"\xff\xfb" + b"a" * 3000
    lines = [{"code": 0, "data": base64.b64encode(audio[:1500]).decode()},
             {"code": 0, "data": base64.b64encode(audio[1500:]).decode()},
             {"code": 20000000, "message": "ok", "data": None}]
    seen = {}
    _use_transport(monkeypatch, _mock_transport(lines, seen))
    dest = await volc_tts.synthesize("你好世界", "zh_female_vv_uranus_bigtts", tmp_path / "a.mp3")
    assert dest.read_bytes() == audio
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["headers"]["x-api-resource-id"] == "seed-tts-2.0"
    req = seen["body"]["req_params"]
    assert req["speaker"] == "zh_female_vv_uranus_bigtts"
    assert req["audio_params"]["speech_rate"] == 10
    assert json.loads(req["additions"])["context_texts"] == ["热情口播"]
    assert patched and patched[0][0][0] == "avatar_tts"


@pytest.mark.asyncio
async def test_no_instruction_for_v1_voice(monkeypatch, tmp_path, patched):
    """1.0 音色不带语气指令。"""
    lines = [{"code": 0, "data": base64.b64encode(b"x" * 2000).decode()}, {"code": 20000000}]
    seen = {}
    _use_transport(monkeypatch, _mock_transport(lines, seen))
    await volc_tts.synthesize("你好", "zh_female_cancan_mars_bigtts", tmp_path / "b.mp3")
    assert "additions" not in seen["body"]["req_params"]


@pytest.mark.asyncio
async def test_error_code_translated(monkeypatch, tmp_path, patched):
    """音色无权限转成中文提示，不写文件。"""
    seen = {}
    _use_transport(monkeypatch, _mock_transport([{"code": 45000000, "message": "speaker permission denied"}], seen))
    with pytest.raises(RuntimeError, match="没有这个音色的权限"):
        await volc_tts.synthesize("你好", "zh_female_vv_uranus_bigtts", tmp_path / "c.mp3")
    assert not (tmp_path / "c.mp3").exists()
