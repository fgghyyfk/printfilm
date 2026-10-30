"""数字人形象 / 场景 / 文案用的 OpenAI 兼容服务客户端（默认 promptoll）。

- 保脸改图：POST {base}/images/edits（multipart：一张参考图 + 提示词 + 模型），用于定妆照与每段场景图
- 文本：POST {base}/chat/completions，用于写口播分段、场景描述与重点文字
- 配音：POST {base}/audio/speech，OpenAI 音色（openai_shimmer 等）

地址、Key、默认模型在管理后台「系统设置 → 数字人」配置（avatar_ai_* / avatar_image_model / avatar_text_model）。
与主流水线的 TokenFree 渠道相互独立；未配 Key 时 is_configured() 为 False，调用方回退到原有逻辑。
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import httpx

from app.config import get_settings
from app.services.tasks.upstream import observed_request

logger = logging.getLogger(__name__)

# 后台/前端下拉里的推荐改图模型（也允许手填其它模型名）
IMAGE_MODELS: list[dict[str, str]] = [
    {"id": "gemini-3.1-flash-image", "label": "Gemini Flash Image", "hint": "卡通化保脸最稳；真人照片常被拒绝改图"},
    {"id": "gpt-image-2", "label": "GPT Image 2", "hint": "画质最好，真人照片（保持原样）推荐用它"},
    {"id": "qwen-image-edit-plus", "label": "通义千问改图 Plus", "hint": "便宜，中文提示友好"},
]


class AvatarAINotConfigured(RuntimeError):
    """后台未配置数字人素材服务 Key。"""


def is_configured() -> bool:
    """是否已配置素材服务（地址 + Key）。"""
    s = get_settings()
    return bool((s.avatar_ai_base_url or "").strip() and (s.avatar_ai_api_key or "").strip())


def resolve_image_model(project_model: str | None = None) -> str:
    """项目指定的改图模型优先，否则后台默认。"""
    return (project_model or "").strip() or (get_settings().avatar_image_model or "").strip() \
        or IMAGE_MODELS[0]["id"]


def _base_and_headers() -> tuple[str, dict[str, str]]:
    """服务地址与鉴权头；未配置时抛 AvatarAINotConfigured。"""
    if not is_configured():
        raise AvatarAINotConfigured("数字人素材服务未配置：请在管理后台「系统设置 → 数字人」填写形象生成的 API Key")
    s = get_settings()
    return s.avatar_ai_base_url.strip().rstrip("/"), {"Authorization": f"Bearer {s.avatar_ai_api_key.strip()}"}


def _detail(resp: httpx.Response) -> str:
    """提取上游错误说明（不含堆栈）。"""
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(data.get("detail") or data)[:300]
    except ValueError:
        return resp.text[:300]


async def _image_bytes_from_response(data: dict, client: httpx.AsyncClient) -> bytes:
    """从 OpenAI 图片响应取第一张图：b64_json 优先，否则下载 url。"""
    items = data.get("data") or []
    if not items:
        raise RuntimeError("改图服务没有返回图片（模型可能拒绝处理真人照片，可换 GPT Image 2 再试）")
    first = items[0] or {}
    if first.get("b64_json"):
        return base64.b64decode(first["b64_json"])
    url = str(first.get("url") or "")
    if url.startswith("data:image"):
        return base64.b64decode(url.split(",", 1)[1])
    if url.startswith("http"):
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
    raise RuntimeError("改图服务返回的图片格式无法识别")


async def edit_image(ref: Path, prompt: str, dest: Path, *, model: str | None = None) -> Path:
    """以 ref 为参考图按提示词改图，结果写到 dest 并返回。

    model 为空时用后台默认；失败抛 RuntimeError（中文短句，可直接展示给用户）。
    """
    base, headers = _base_and_headers()
    s = get_settings()
    name = resolve_image_model(model)
    data = {"model": name, "prompt": prompt[:3800], "n": "1", "size": s.avatar_image_size or "1024x1536",
            "response_format": "b64_json"}
    timeout = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=30.0)
    mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
    async with httpx.AsyncClient(timeout=timeout) as client:
        with ref.open("rb") as fh:
            resp = await observed_request(client, "POST", f"{base}/images/edits", provider="avatar_ai",
                                          headers=headers, data=data, files={"image": (ref.name, fh, mime)})
        if resp.status_code >= 400:
            raise RuntimeError(f"改图失败（{name}，HTTP {resp.status_code}）：{_detail(resp)}")
        raw = await _image_bytes_from_response(resp.json(), client)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    logger.info("avatar_ai edit model=%s ref=%s -> %s (%d bytes)", name, ref.name, dest.name, len(raw))
    from app.services.kepu_avatar_billing import image_cost_fen, record_avatar_ai

    await record_avatar_ai("avatar_image", model=name, cost_fen=image_cost_fen(name), detail={"images": 1})
    return dest


async def speech(text: str, voice: str, dest: Path, *, model: str = "tts-1-hd") -> Path:
    """OpenAI 兼容配音：POST {base}/audio/speech，mp3 写到 dest。失败抛 RuntimeError。"""
    base, headers = _base_and_headers()
    payload = {"model": model, "voice": voice, "input": text[:4000], "response_format": "mp3"}
    timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await observed_request(client, "POST", f"{base}/audio/speech", provider="avatar_ai",
                                      headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"配音失败（{model}/{voice}，HTTP {resp.status_code}）：{_detail(resp)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    logger.info("avatar_ai speech model=%s voice=%s -> %s (%d bytes)", model, voice, dest.name, len(resp.content))
    from app.services.kepu_avatar_billing import record_avatar_ai, speech_cost_fen

    await record_avatar_ai("avatar_tts", model=f"{model}/{voice}", cost_fen=speech_cost_fen(len(text)),
                           detail={"chars": len(text)})
    return dest


def parse_json_reply(text: str) -> dict:
    """从模型回复里取出 JSON 对象（容忍 ```json 包裹与前后说明文字）。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON")
    return json.loads(raw[start:end + 1])


async def chat_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 4000) -> dict:
    """调文本模型并解析 JSON 回复；解析失败重试一次。"""
    base, headers = _base_and_headers()
    name = (model or get_settings().avatar_text_model or "qwen3.6-plus").strip()
    payload = {"model": name, "temperature": 0.7, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=30.0)
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(2):
            resp = await observed_request(client, "POST", f"{base}/chat/completions", provider="avatar_ai",
                                          headers=headers, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"文案生成失败（{name}，HTTP {resp.status_code}）：{_detail(resp)}")
            body = resp.json()
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            from app.services.kepu_avatar_billing import record_avatar_ai

            await record_avatar_ai("llm_chat", model=name, tokens=int(usage.get("total_tokens") or 0))
            try:
                return parse_json_reply(content)
            except ValueError as exc:  # json.JSONDecodeError 也是 ValueError
                last_err = exc
                logger.warning("avatar_ai chat json parse failed model=%s: %s", name, exc)
    raise RuntimeError(f"文案生成失败：模型返回的格式不对（{last_err}）")
