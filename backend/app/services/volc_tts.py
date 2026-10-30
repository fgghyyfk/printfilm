"""火山引擎豆包语音合成大模型（V3 HTTP 单向流式）直连。

POST {base}/api/v3/tts/unidirectional，头 X-Api-Key + X-Api-Resource-Id（seed-tts-2.0 等），
响应是一行一个 JSON：音频块 {"code":0,"data":"<base64>"}，结束块 code=20000000。
Key 在管理后台「系统设置 → 数字人 → 配音」配置（volc_tts_api_key）；未配置时 is_configured() 为 False，调用方回退原有配音。
2.0 音色支持「指令遵循」：additions.context_texts 里写语气要求（如「用热情、有感染力的口播语气」）。
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

END_CODE = 20000000
# 常见错误码的中文说明
_ERRORS = {
    40402003: "文本太长，请拆短再配音",
    45000000: "当前 Key 没有这个音色的权限（请在火山控制台开通对应音色/模型）",
    45000030: "火山账号还没开通语音合成服务（控制台 → 豆包语音 → 开通「语音合成大模型 2.0」）",
    55000000: "火山语音服务内部错误，请稍后重试",
}


def is_configured() -> bool:
    """是否已配置火山语音 Key。"""
    return bool((get_settings().volc_tts_api_key or "").strip())


def is_volc_speaker(speaker: str) -> bool:
    """豆包音色 id（*_bigtts、ICL_*、saturn_* / S_ 复刻）才走火山。"""
    s = (speaker or "").strip()
    return s.endswith("_bigtts") or s.endswith("_tob") or s.startswith(("ICL_", "saturn_", "S_"))


def resource_for(speaker: str) -> str:
    """按音色挑资源：2.0（uranus / ICL_uranus）走 seed-tts-2.0，其余按后台默认。"""
    s = (speaker or "").strip()
    if "uranus" in s:
        return "seed-tts-2.0"
    if s.startswith("S_"):
        return "seed-icl-2.0"
    return (get_settings().volc_tts_resource_id or "seed-tts-1.0").strip()


async def synthesize(text: str, speaker: str, dest: Path, *, speech_rate: int | None = None,
                     instruction: str | None = None) -> Path:
    """合成一段 mp3 写到 dest。speech_rate：-50~100（0 为正常）；instruction：2.0 音色的语气指令。

    失败抛 RuntimeError（中文短句）。
    """
    s = get_settings()
    key = (s.volc_tts_api_key or "").strip()
    if not key:
        raise RuntimeError("未配置火山引擎语音 Key")
    rate = int(s.volc_tts_speech_rate if speech_rate is None else speech_rate)
    hint = (s.volc_tts_instruction if instruction is None else instruction) or ""
    audio_params: dict = {"format": "mp3", "sample_rate": 24000, "speech_rate": max(-50, min(100, rate))}
    req: dict = {"text": text, "speaker": speaker, "audio_params": audio_params}
    if hint.strip() and "uranus" in speaker:
        req["additions"] = json.dumps({"context_texts": [hint.strip()]}, ensure_ascii=False)
    body = {"user": {"uid": "printfilm"}, "req_params": req}
    headers = {"X-Api-Key": key, "X-Api-Resource-Id": resource_for(speaker),
               "X-Api-Request-Id": uuid.uuid4().hex, "Content-Type": "application/json"}
    url = f"{(s.volc_tts_base_url or 'https://openspeech.bytedance.com').rstrip('/')}/api/v3/tts/unidirectional"
    chunks: list[bytes] = []
    timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                raw = (await resp.aread()).decode("utf-8", "ignore")
                try:
                    code = int(((json.loads(raw).get("header") or {}).get("code")) or 0)
                except (ValueError, AttributeError):
                    code = 0
                reason = _ERRORS.get(code) or raw[:200]
                raise RuntimeError(f"火山配音失败（HTTP {resp.status_code}）：{reason}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                code = int(msg.get("code") or 0)
                if code == END_CODE:
                    break
                if code != 0:
                    reason = _ERRORS.get(code) or str(msg.get("message") or code)[:200]
                    raise RuntimeError(f"火山配音失败：{reason}")
                if msg.get("data"):
                    chunks.append(base64.b64decode(msg["data"]))
    audio = b"".join(chunks)
    if len(audio) < 1000:
        raise RuntimeError("火山配音失败：没有返回音频")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)
    logger.info("volc tts speaker=%s chars=%d -> %s (%d bytes)", speaker, len(text), dest.name, len(audio))
    from app.services.kepu_avatar_billing import record_avatar_ai, volc_tts_cost_fen

    await record_avatar_ai("avatar_tts", model=f"volc/{speaker}", cost_fen=volc_tts_cost_fen(len(text)),
                           detail={"chars": len(text)})
    return dest
