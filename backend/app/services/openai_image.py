"""生图改走 OpenAI（gpt-image）：可选旁路，火山等主渠道继续负责文本与视频。

渠道二选一（后台「系统设置 → 生图」）：OpenAI 官方，或自有网关 Promptoll（https://api.promptoll.com/v1，
Key 以 zai_ 开头）。Promptoll 的 edits 默认回 URL，这里显式要 b64_json，回 URL 时也能下载；
Promptoll 在响应头 X-Price-Microcents 回传实际成本（1 美元 = 1 亿），写进用量供计费核对。

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
from app.services.url_guard import guard_request

logger = logging.getLogger(__name__)

SCOPES ={"tools", "all"}
QUALITIES = {"low", "medium", "high", "auto"}
TARGET_PIXELS = 1536 * 1536          # 约 2.36MP：清晰度够用、成本可控
MAX_REFS = 16
REF_MAX_EDGE = 2048                  # 参考图长边上限（线上到 OpenAI 带宽充足，保留细节）


PROMPTOLL_BASE_URL = "https://api.promptoll.com/v1"
PROVIDER_LABEL = {"openai": "OpenAI", "promptoll": "Promptoll"}


def image_endpoint(settings: Settings | None = None) -> tuple[str, str, str]:
    """当前生图渠道：返回 (provider, base_url, api_key)。"""
    s = settings or get_settings()
    if (s.openai_image_provider or "openai").strip() == "promptoll":
        return "promptoll", PROMPTOLL_BASE_URL, (s.promptoll_api_key or "").strip()
    return "openai", (s.openai_image_base_url or "https://api.openai.com/v1").rstrip("/"), (s.openai_image_api_key or "").strip()


def provider_label(settings: Settings | None = None) -> str:
    """渠道中文/品牌名（日志、报错、前台选项用）。"""
    return PROVIDER_LABEL.get(image_endpoint(settings)[0], "OpenAI")


def is_openai_image_model(model_id: str | None) -> bool:
    """前台所选模型是否为 OpenAI 生图（gpt-image-*）。"""
    return (model_id or "").strip().lower().startswith("gpt-image")


def openai_image_configured(settings: Settings | None = None) -> bool:
    """后台已开启 OpenAI 生图且填了 Key（与作用范围无关）。"""
    s = settings or get_settings()
    return bool(s.openai_image_enabled and image_endpoint(s)[2])


def openai_image_active(purpose: str, settings: Settings | None = None) -> bool:
    """当前这次生图是否改走 OpenAI：已开启、填了 Key，且作用范围覆盖本次用途（tools=工具中心）。"""
    s = settings or get_settings()
    if not openai_image_configured(s):
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
                                     event_hooks={"request": [guard_request]}) as client:
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


def _error_text(resp: httpx.Response, label: str = "OpenAI") -> str:
    """把 OpenAI / Promptoll 错误转成中文短句（不回传 Key 等敏感信息）。"""
    try:
        body = resp.json()
        err = body.get("error") or {}
        if isinstance(err, str):
            err = {"message": err}
        msg = str(err.get("message") or body.get("detail") or "")
        code = str(err.get("code") or err.get("type") or "")
    except Exception:  # noqa: BLE001
        msg, code = resp.text[:200], ""
    head = f"{label} 生图失败"
    if resp.status_code == 401:
        return f"{head}：API Key 无效或已失效，请在后台重新填写"
    if resp.status_code == 402:
        return f"{head}：{label} 账户余额不足，请先充值"
    if resp.status_code == 429 and ("quota" in msg.lower() or "credit" in code.lower() or "balance" in msg.lower()):
        return f"{head}：API 额度已用完，请充值后再试"
    if resp.status_code == 429:
        return f"{head}：请求过于频繁，请稍后重试"
    if "moderation" in msg.lower() or code == "moderation_blocked":
        return f"{head}：内容未通过安全审核，请调整提示词或参考图"
    if label == "Promptoll" and resp.status_code == 400 and "-hd" in msg:
        return f"{head}：这个 Key 未开通 gpt-image-2 高画质，请在后台把画质改为「中」，或为 Key 开通官方 OpenAI 路由"
    return f"{head}（{resp.status_code}）：{msg[:200]}"


def _hd_unsupported(resp: httpx.Response) -> bool:
    """Promptoll 默认路由把 quality=high/hd 计成 <model>-hd，目录里没有就回 400。"""
    return resp.status_code == 400 and "-hd" in resp.text


def _cost_usd(resp: httpx.Response) -> float | None:
    """Promptoll 响应头 X-Price-Microcents → 美元（1 美元 = 1 亿 microcents）。"""
    raw = resp.headers.get("x-price-microcents")
    try:
        return int(raw) / 100_000_000 if raw not in (None, "") else None
    except ValueError:
        return None


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
    provider, base, api_key = image_endpoint(s)
    label = PROVIDER_LABEL[provider]
    model = (s.openai_image_model or "gpt-image-2").strip()
    quality = (s.openai_image_quality or "high").strip()
    if quality not in QUALITIES:
        quality = "high"
    out_size = openai_size(ratio_hint, size)
    headers = {"Authorization": f"Bearer {api_key}"}
    refs = list(dict.fromkeys(ref_urls or []))[: (8 if provider == "promptoll" else MAX_REFS)]
    timeout = httpx.Timeout(connect=20, read=300, write=120, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if refs:
            files = []
            for url in refs:
                name, data = await _ref_bytes(url)
                files.append(("image[]", (name, data, "image/png")))
            form = {"model": model, "prompt": prompt, "size": out_size, "quality": quality, "n": "1"}
            if provider == "promptoll":
                form["response_format"] = "b64_json"  # Promptoll 默认回 URL
            resp = await client.post(f"{base}/images/edits", headers=headers, data=form, files=files)
        else:
            body = {"model": model, "prompt": prompt, "size": out_size, "quality": quality, "n": 1}
            if provider == "promptoll":
                body["response_format"] = "b64_json"
            resp = await client.post(f"{base}/images/generations", headers=headers, json=body)
            if provider == "promptoll" and _hd_unsupported(resp):
                # 普通 Key 不支持高画质（会被记成 <model>-hd）：去掉 quality 重试一次，用默认画质出图
                logger.info("Promptoll 不支持 quality=%s，改用默认画质重试", quality)
                body.pop("quality", None)
                resp = await client.post(f"{base}/images/generations", headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(_error_text(resp, label))
        data = resp.json()
        item = (data.get("data") or [{}])[0]
        b64 = item.get("b64_json")
        if b64:
            content = base64.b64decode(b64)
        elif item.get("url"):
            got = await client.get(item["url"])
            got.raise_for_status()
            content = got.content
        else:
            raise RuntimeError(f"{label} 生图未返回图片，请稍后重试")
    dest = storage.project_dir(project_id or 0) / f"shot_{(shot_no or 0):03d}_{uuid.uuid4().hex[:12]}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    usage = dict(data.get("usage") or {})
    cost = _cost_usd(resp)
    if cost is not None:
        usage["cost_usd"] = cost
    logger.info("%s 生图完成 model=%s size=%s refs=%s cost_usd=%s -> %s", label, model, out_size, len(refs), cost, dest.name)
    return {"local_path": dest, "size": out_size, "model": model, "usage": usage, "provider": provider}


def save_path_url(path: Path) -> str:
    """落盘文件转站内地址（开 OSS 时同步上传）。"""
    return storage.publish_local(path, sync=True)
