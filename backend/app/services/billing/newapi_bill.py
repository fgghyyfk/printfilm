"""用 TokenFree New API 消费日志的 quota 覆盖本地报价。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models import UsageEvent
from app.services.tokenfree_usage import fetch_tokenfree_consume_logs, quota_to_cost_fen

logger = logging.getLogger(__name__)

_LOOKBACK = timedelta(minutes=3)
_LOOKAHEAD = timedelta(seconds=90)


def select_consume_log(
    event: UsageEvent,
    logs: list[dict[str, Any]],
    used_ids: set[int],
) -> dict[str, Any] | None:
    """为一条用量选唯一对应的消费日志；对不上或有多条候选则放弃。"""
    pool = [item for item in logs if _usable(item, used_ids) and _same_model(event, item)]
    task_id = _provider_task_id(event)
    if task_id:
        hits = [item for item in pool if task_id in json.dumps(item, ensure_ascii=False)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            pool = hits
    compatible = [item for item in pool if _tokens_compatible(event, item)]
    prompt = int(getattr(event, "prompt_tokens", 0) or 0)
    completion = int(getattr(event, "completion_tokens", 0) or 0)
    if prompt or completion:
        exact = [
            item
            for item in compatible
            if int(item.get("prompt_tokens") or 0) == prompt
            and int(item.get("completion_tokens") or 0) == completion
        ]
        if len(exact) == 1:
            return exact[0]
    if len(compatible) == 1:
        return compatible[0]
    return None


def apply_log_quota(event: UsageEvent, log: dict[str, Any]) -> bool:
    """把日志 quota 写成该用量的实扣。quota 为 0 时不改。"""
    try:
        quota = int(log.get("quota") or 0)
    except (TypeError, ValueError):
        return False
    if quota <= 0:
        return False
    fen = quota_to_cost_fen(quota)
    if fen <= 0:
        return False
    event.cost_fen = fen
    if not (getattr(event, "billing_detail", None) or {}).get("mode") == "fixed_media":
        event.charge_fen = fen
    event.cost_status = "verified"
    event.estimated = False
    raw = _load_raw(event)
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    usage = dict(usage)
    usage["quota"] = quota
    raw["usage"] = usage
    raw["billing_basis"] = "fixed_media" if (getattr(event, "billing_detail", None) or {}).get("mode") == "fixed_media" else "newapi_log"
    if log.get("id") is not None:
        raw["newapi_log_id"] = log.get("id")
    event.raw_usage_json = json.dumps(raw, ensure_ascii=False)[:4000]
    return True


async def apply_newapi_bills(events: list[UsageEvent]) -> int:
    """结算前按时间窗拉消费日志，能唯一对上的改成账单 quota。"""
    pending = [event for event in events if not _already_billed(event) and not _load_raw(event).get("simulated")]
    if not pending:
        return 0
    start, end = _window(pending)
    if start is None or end is None:
        return 0
    try:
        logs = await fetch_tokenfree_consume_logs(int(start.timestamp()), int(end.timestamp()))
    except Exception:  # noqa: BLE001
        logger.info("拉取 TokenFree 消费日志失败，沿用价目报价")
        return 0
    if not logs:
        return 0
    used: set[int] = set()
    applied = 0
    for event in pending:
        chosen = select_consume_log(event, logs, used)
        if chosen is None:
            continue
        if apply_log_quota(event, chosen):
            try:
                used.add(int(chosen.get("id")))
            except (TypeError, ValueError):
                pass
            applied += 1
    if applied:
        logger.info("New API 账单覆盖用量 %s 条", applied)
    return applied


def _usable(item: dict[str, Any], used_ids: set[int]) -> bool:
    try:
        quota = int(item.get("quota") or 0)
    except (TypeError, ValueError):
        return False
    if quota <= 0:
        return False
    try:
        log_id = int(item.get("id"))
    except (TypeError, ValueError):
        log_id = None
    return log_id is None or log_id not in used_ids


def _same_model(event: UsageEvent, item: dict[str, Any]) -> bool:
    want = (getattr(event, "model", "") or "").strip().lower()
    got = str(item.get("model_name") or "").strip().lower()
    if not want or not got:
        return True
    return want == got


def _tokens_compatible(event: UsageEvent, item: dict[str, Any]) -> bool:
    prompt = int(getattr(event, "prompt_tokens", 0) or 0)
    completion = int(getattr(event, "completion_tokens", 0) or 0)
    if prompt + completion <= 0:
        return True
    try:
        log_prompt = int(item.get("prompt_tokens") or 0)
        log_completion = int(item.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return True
    if log_prompt + log_completion <= 0:
        return True
    return prompt == log_prompt and completion == log_completion


def _provider_task_id(event: UsageEvent) -> str:
    raw = _load_raw(event)
    text = str(raw.get("provider_task_id") or "").strip()
    return text


def _already_billed(event: UsageEvent) -> bool:
    raw = _load_raw(event)
    return raw.get("newapi_log_id") is not None or raw.get("billing_basis") == "newapi_log"


def _load_raw(event: UsageEvent) -> dict[str, Any]:
    text = getattr(event, "raw_usage_json", None)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _window(events: list[UsageEvent]) -> tuple[datetime | None, datetime | None]:
    stamps = [_as_utc(getattr(event, "created_at", None)) for event in events]
    stamps = [item for item in stamps if item is not None]
    if not stamps:
        return None, None
    return min(stamps) - _LOOKBACK, max(stamps) + _LOOKAHEAD


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
