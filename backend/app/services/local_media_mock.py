"""Local media fixtures with sanitized request logging; never calls a provider."""
from __future__ import annotations

import asyncio
import json
import uuid
from threading import Lock
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.services.upstream_sanitize import sanitize_upstream_value

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT.parent / ".tmp" / "local-ai-requests.jsonl"
ASSET_ROOT = ROOT / "static" / "mock" / "local"
VIDEO_PREFIX = "local-media-"
_log_lock = Lock()


def media_url(name: str) -> str:
    if name not in {"default.mp4", "default.jpg", "default.mp3"}:
        raise ValueError("Unknown mock media fixture")
    if not (ASSET_ROOT / name).is_file():
        raise RuntimeError("缺少本地模拟素材，请运行 backend/scripts/prepare_mock_media.py")
    return f"/static/mock/local/{name}"


def _append_request(record: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def record_request(operation: str, body: dict[str, Any], *, method: str | None = None,
                         endpoint: str | None = None, headers: dict | None = None) -> str:
    from app.services.billing.context import get_current_task_run_id
    from app.services.tasks.upstream import _safe_record

    request_id = uuid.uuid4().hex
    task_id = get_current_task_run_id()
    # Retain the same team authorization barrier as real upstream submissions.
    if task_id is not None:
        from app.database import AsyncSessionLocal
        from app.services.tasks.upstream import _event_session
        from app.services.tasks.team_scope import ensure_upstream_submission_allowed
        caller = _event_session.get()
        if caller is not None:
            await ensure_upstream_submission_allowed(caller, task_id)
        else:
            async with AsyncSessionLocal() as db:
                await ensure_upstream_submission_allowed(db, task_id)
    record = sanitize_upstream_value({
        "timestamp": datetime.now(UTC).isoformat(), "request_id": request_id,
        "task_id": task_id, "operation": operation, "simulated": True,
        "method": method, "endpoint": endpoint, "request_stage": "http_ready" if endpoint else "business_input",
        "request": {"headers": headers or {}, "body": body},
    })
    await asyncio.to_thread(_append_request, record)
    if task_id is not None:
        await _safe_record(task_id, "upstream.request.simulated", {
            **record, "provider": "local-mock", "model": body.get("model"),
            "message": "本地模拟，未发送至上游",
        })
    return VIDEO_PREFIX + request_id


async def simulate_http_request(method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
    """Capture the exact prepared HTTP arguments, then return a local provider response."""
    path = urlsplit(endpoint).path.rstrip("/")
    body = kwargs.get("json") or {}
    if path.endswith(("/videos", "/contents/generations/tasks")):
        operation = "video.generate"
    elif path.endswith(("/images/generations", "/responses")):
        operation = "image.generate"
    elif path.endswith(("/audio/speech", "/chat/completions")):
        operation = "audio.tts"
    else:
        raise RuntimeError(f"Unsupported local media mock endpoint: {path}")
    ident = await record_request(operation, body, method=method.upper(), endpoint=endpoint, headers=kwargs.get("headers"))
    request = httpx.Request(method, endpoint)
    if operation == "video.generate":
        media_url("default.mp4")
        return httpx.Response(200, json={"id": ident, "status": "queued", "simulated": True}, request=request)
    if operation == "image.generate":
        return httpx.Response(200, json={"status": "completed", "data": [{"url": media_url("default.jpg")}], "simulated": True}, request=request)
    return httpx.Response(200, content=(ASSET_ROOT / "default.mp3").read_bytes(), headers={"content-type": "audio/mpeg"}, request=request)
