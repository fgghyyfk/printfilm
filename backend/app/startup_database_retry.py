"""Bounded connection recovery for idempotent database startup steps only."""

from __future__ import annotations

import asyncio
import errno
import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.exc import DBAPIError, DisconnectionError

logger = logging.getLogger(__name__)

_TRANSIENT_SQLSTATES = frozenset({"08003", "08006", "08007", "57P01", "57P02", "57P03"})
_NETWORK_ERRNOS = frozenset(
    {errno.ECONNRESET, errno.ECONNABORTED, errno.ENETRESET, errno.EPIPE, errno.ETIMEDOUT, errno.ECONNREFUSED}
)
_NETWORK_WINERRORS = frozenset({121, 10053, 10054, 10060, 10061})
_MAX_ATTEMPTS = 3


def is_transient_database_disconnect(error: BaseException) -> bool:
    """Recognize driver disconnects without treating every DB error as transient.

    Inspect SQLAlchemy's driver wrapper and chained OS errors. A definite server
    error such as invalid credentials, syntax or a missing table takes priority
    over a generic connection-invalidated flag.
    """
    pending = [error]
    seen: set[int] = set()
    disconnected = False
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate:
            if sqlstate not in _TRANSIENT_SQLSTATES:
                return False
            disconnected = True
        if isinstance(current, DisconnectionError) or (
            isinstance(current, DBAPIError) and current.connection_invalidated
        ):
            disconnected = True
        if isinstance(current, OSError) and (
            getattr(current, "winerror", None) in _NETWORK_WINERRORS
            or current.errno in _NETWORK_ERRNOS
        ):
            disconnected = True
        original = getattr(current, "orig", None)
        if isinstance(original, BaseException):
            pending.append(original)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)
    return disconnected


async def run_database_startup_steps(
    steps: Sequence[Callable[[], Awaitable[object]]],
    *,
    dispose: Callable[[], Awaitable[None]],
) -> None:
    """Retry the failed step at most three times; never replay successful steps.

    Callers must supply only idempotent DB initialization callbacks. Runtime and
    external service startup belong outside this function. Dispose after every
    disconnect, including the final failure, so no broken pool remains reusable.
    """
    for step in steps:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                await step()
            except Exception as error:
                if not is_transient_database_disconnect(error):
                    raise
                await dispose()
                if attempt == _MAX_ATTEMPTS:
                    raise
                delay = 0.25 * (2 ** (attempt - 1))
                logger.warning(
                    "Database startup step %s disconnected (%s), retry %s/%s in %.2fs",
                    getattr(step, "__name__", type(step).__name__),
                    type(error).__name__,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                break
