"""全集重新生成：新稿写在 params 草稿里，全部成功后才替换旧正文。"""
from __future__ import annotations

from typing import Any

DRAFT_KEY = "episode_regen_draft"


def _episode_number(item: Any) -> int:
    """取集号；异常数据按 0 处理并由调用方跳过。"""
    if not isinstance(item, dict):
        return 0
    try:
        return int(item.get("episodeNumber") or 0)
    except (TypeError, ValueError):
        return 0


def regen_working_episodes(existing: list, script_params: dict | None) -> list[dict[str, Any]]:
    """返回本轮工作稿：进程重启后续用已写好的草稿，否则从旧稿去掉正文，保留标题、梗概、备注和来源。"""
    saved = (script_params or {}).get(DRAFT_KEY)
    if isinstance(saved, list) and saved:
        return list(saved)
    working: list[dict[str, Any]] = []
    for item in existing:
        number = _episode_number(item)
        if number < 1:
            continue
        row: dict[str, Any] = {
            "episodeNumber": number,
            "title": str(item.get("title") or f"第 {number} 集"),
            "body": "",
        }
        for key in ("summary", "creative", "origin"):
            if item.get(key):
                row[key] = item[key]
        working.append(row)
    return working


def finalize_regen_episodes(current: list, draft: list) -> list[dict[str, Any]]:
    """新稿成功后的最终内容；手动集不参与自动重写，草稿正文为空时保留现有正文。"""
    bodies = {
        _episode_number(item): str(item.get("body") or item.get("content") or "")
        for item in current
        if _episode_number(item) >= 1
    }
    final: list[dict[str, Any]] = []
    for item in draft:
        number = _episode_number(item)
        if number < 1:
            continue
        row = dict(item)
        if not str(row.get("body") or "").strip():
            row["body"] = bodies.get(number, "")
        final.append(row)
    return final
