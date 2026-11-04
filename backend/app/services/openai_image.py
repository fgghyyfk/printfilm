"""生图改走 OpenAI（gpt-image）：可选旁路，火山等主渠道继续负责文本与视频。

- 有参考图 → POST /v1/images/edits（multipart，重复 image[] 字段，最多 16 张）
- 无参考图 → POST /v1/images/generations
- 结果为 base64，落盘到 static/generated/p{project}/ 后与主渠道一样返回本地地址
"""

from __future__ import annotations

import base64
import io
import logging
import math
import re
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from app.config import Settings, get_settings
from app.services import storage

logger = logging.getLogger(__name__)

SCOPES = {"tools", "all"}
QUALITIES = {"low", "medium", "high", "auto"}
TARGET_PIXELS = 1536 * 1536          # 约 2.36MP：清晰度够用、成本可控
MAX_REFS = 16
REF_MAX_EDGE = 2048                  # 参考图长边上限（线上到 OpenAI 带宽充足，保留细节）


def is_openai_image_model(model_id: str | None) -> bool:
    """前台所选模型是否为 OpenAI 生图（gpt-image-*）。"""
    return (model_id or "").strip().lower().startswith("gpt-image")


def openai_image_configured(settings: Settings | None = None) -> bool:
    """后台已开启 OpenAI 生图且填了 Key（与作用范围无关）。"""
    s = settings or get_settings()
    return bool(s.openai_image_enabled and (s.openai_image_api_key or "").strip())


def openai_image_active(purpose: str, settings: Settings | None = None) -> bool:
    """当前这次生图是否改走 OpenAI：已开启、填了 Key，且作用范围覆盖本次用途（tools=工具中心）。"""
    s = settings or get_settings()
    if not (s.openai_image_enabled and (s.openai_image_api_key or "").strip()):
        return False
    scope = (s.openai_image_scope or "tools").strip()
    return scope == "all" or (scope == "tools" and purpose == "tools")


def _ratio_from(ratio_hint: str | None, size: str | None) -> float:
    """从「3:4」类画幅或「1728x2304」类尺寸推出宽高比，默认 1:1。"""
    for raw, sep in ((ratio_hint, ":"), (size, "x")):
        m = re.fullmatch(rf"\s*(\d+(?:\.\d+)?)\s*{sep}\s*(\d+(?:\.\d+)?)\s*", (raw or "").lower())
        if m and float(m.group(2)) > 0:
            return float(m.group(1)) / float(m.group(2))
    return 1.0


def openai_size(ratio_hint: str | None, size: str | None) -> str:
    """换算成 gpt-image 支持的 WxH：总像素约 2.36MP，宽高为 16 的倍数，宽高比限制在 1:3–3:1。"""
    r = min(max(_ratio_from(ratio_hint, size), 1 / 3), 3.0)
    h = math.sqrt(TARGET_PIXELS / r)
    w = h * r
    w16, h16 = max(16, round(w / 16) * 16), max(16, round(h / 16) * 16)
    return f"{w16}x{h16}"


async def _ref_bytes(url: str) -> tuple[str, bytes]:
    """读取参考图（本地 /static 或 https），统一转为长边 ≤2048 的 PNG，返回 (文件名, 字节)。"""
    local = storage.local_path_from_url(url)
    if local is not None and local.exists():
        raw = local.read_bytes()
    elif url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content
    elif url.startswith("data:"):
        raw = base64.b64decode(url.split(",", 1)[1])
    else:
        raise ValueError(f"参考图无法读取：{url[:80]}")
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        im.thumbnail((REF_MAX_EDGE, REF_MAX_EDGE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
    return f"ref_{uuid.uuid4().hex[:8]}.png", buf.getvalue()


def _error_text(resp: httpx.Response) -> str:
    """把 OpenAI 错误转成中文短句（不回传 Key 等敏感信息）。"""
    try:
        err = resp.json().get("error") or {}
        msg, code = str(err.get("message") or ""), str(err.get("code") or err.get("type") or "")
    except Exception:  # noqa: BLE001
        msg, code = resp.text[:200], ""
    if resp.status_code == 401:
        return "OpenAI 生图失败：API Key 无效或已失效，请在后台重新填写"
    if resp.status_code == 429 and ("quota" in msg.lower() or "credit" in code.lower() or "balance" in msg.lower()):
        return "OpenAI 生图失败：API 额度已用完，请在 OpenAI 后台充值或等待下月额度"
    if resp.status_code == 429:
        return "OpenAI 生图失败：请求过于频繁，请稍后重试"
    if "moderation" in msg.lower() or code == "moderation_blocked":
        return "OpenAI 生图失败：内容未通过安全审核，请调整提示词或参考图"
    return f"OpenAI 生图失败（{resp.status_code}）：{msg[:200]}"


async def generate(
    prompt: str,
    ref_urls: list[str] | None,
    *,
    project_id: int | None,
    shot_no: int | None,
    size: str | None,
    ratio_hint: str | None = None,
    settings: Settings | None = None,
) -> dict:
    """调用 OpenAI 生图并落盘。返回 {local_path, size, model, usage}。"""
    s = settings or get_settings()
    base = (s.openai_image_base_url or "https://api.openai.com/v1").rstrip("/")
    model = (s.openai_image_model or "gpt-image-2").strip()
    quality = (s.openai_image_quality or "high").strip()
    if quality not in QUALITIES:
        quality = "high"
    out_size = openai_size(ratio_hint, size)
    headers = {"Authorization": f"Bearer {s.openai_image_api_key.strip()}"}
    refs = list(dict.fromkeys(ref_urls or []))[:MAX_REFS]
    timeout = httpx.Timeout(connect=20, read=300, write=120, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if refs:
            files = []
            for url in refs:
                name, data = await _ref_bytes(url)
                files.append(("image[]", (name, data, "image/png")))
            form = {"model": model, "prompt": prompt, "size": out_size, "quality": quality, "n": "1"}
            resp = await client.post(f"{base}/images/edits", headers=headers, data=form, files=files)
        else:
            body = {"model": model, "prompt": prompt, "size": out_size, "quality": quality, "n": 1}
            resp = await client.post(f"{base}/images/generations", headers=headers, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(_error_text(resp))
    data = resp.json()
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI 生图未返回图片，请稍后重试")
    dest = storage.project_dir(project_id or 0) / f"shot_{(shot_no or 0):03d}_{uuid.uuid4().hex[:12]}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(b64))
    logger.info("OpenAI 生图完成 model=%s size=%s refs=%s -> %s", model, out_size, len(refs), dest.name)
    return {"local_path": dest, "size": out_size, "model": model, "usage": data.get("usage") or {}}


def save_path_url(path: Path) -> str:
    """落盘文件转站内地址（开 OSS 时同步上传）。"""
    return storage.publish_local(path, sync=True)
