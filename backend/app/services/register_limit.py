"""注册限流：防止脚本批量注册刷赠金。优先用 Redis 跨进程计数，不可用时退回进程内计数。"""
from __future__ import annotations

import ipaddress
import logging
import time

from fastapi import Request

logger = logging.getLogger(__name__)

HOUR_LIMIT = 10  # 同一 IP 每小时最多注册数
GRANT_DAY_LIMIT = 2  # 同一 IP 每天最多领取赠金的账号数
_memory: dict[str, tuple[int, float]] = {}


def client_ip(request: Request | None) -> str:
    """取真实客户端 IP：X-Forwarded-For 从右往左第一个公网地址（左侧可被客户端伪造）。"""
    if request is None:
        return ""
    for raw in reversed((request.headers.get("x-forwarded-for") or "").split(",")):
        try:
            if ipaddress.ip_address(raw.strip()).is_global:
                return raw.strip()
        except ValueError:
            continue
    return (request.headers.get("x-real-ip") or "").strip() or (request.client.host if request.client else "")


def _memory_hit(key: str, ttl: int) -> int:
    """进程内计数；顺带清理过期项，避免长期运行内存增长。"""
    now = time.monotonic()
    for stale in [k for k, (_, until) in _memory.items() if until <= now]:
        _memory.pop(stale, None)
    count, until = _memory.get(key, (0, now + ttl))
    _memory[key] = (count + 1, until)
    return count + 1


def _hit(key: str, ttl: int) -> int:
    """计数加一并返回当前值。"""
    from app.services.password_reset import get_redis_client

    try:
        client = get_redis_client()
        if client is not None:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, ttl)
            return count
    except Exception as exc:  # noqa: BLE001
        logger.warning("register limit redis unavailable: %s", exc)
    return _memory_hit(key, ttl)


def register_allowed(ip: str) -> bool:
    """本次注册是否在小时限额内；取不到 IP 时放行。"""
    return not ip or _hit(f"auth:register:hour:{ip}", 3600) <= HOUR_LIMIT


def grant_allowed(ip: str) -> bool:
    """本账号是否可领赠金；同 IP 超额的账号仍可注册，只是不送。"""
    return not ip or _hit(f"auth:register:grant:{ip}", 86400) <= GRANT_DAY_LIMIT
