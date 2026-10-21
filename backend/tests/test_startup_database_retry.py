"""Startup connection recovery without databases, app startup or network calls."""

import asyncio
import errno
from unittest.mock import AsyncMock, call

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError, DisconnectionError, ProgrammingError

from app.startup_database_retry import (
    is_transient_database_disconnect,
    run_database_startup_steps,
)


def _win121():
    error = OSError("semaphore timeout")
    error.winerror = 121
    return error


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.ConnectionDoesNotExistError("closed during operation"),
        asyncpg.ConnectionFailureError("connection failed"),
        asyncpg.AdminShutdownError("server restarting"),
        asyncpg.CannotConnectNowError("server starting"),
        DBAPIError("SELECT 1", {}, Exception("closed"), connection_invalidated=True),
        DisconnectionError("closed"),
        _win121(),
        ConnectionResetError(errno.ECONNRESET, "reset"),
        ConnectionAbortedError(errno.ECONNABORTED, "aborted"),
    ],
)
def test_only_clear_disconnects_are_transient(error):
    assert is_transient_database_disconnect(error)


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.InvalidPasswordError("bad password"),
        asyncpg.InvalidCatalogNameError("missing database"),
        asyncpg.UndefinedTableError("missing table"),
        asyncpg.InsufficientPrivilegeError("access denied"),
        ProgrammingError("bad SQL", {}, Exception("syntax")),
        DBAPIError("SELECT 1", {}, Exception("unknown driver failure")),
        TimeoutError("a slow query alone does not prove a disconnect"),
        RuntimeError("bad schema configuration"),
        FileNotFoundError("missing seed file"),
    ],
)
def test_authentication_schema_and_other_errors_are_not_retried(error):
    assert not is_transient_database_disconnect(error)


def test_checks_wrapped_driver_and_os_error_without_message_matching():
    driver_error = DBAPIError("SELECT current_schema()", {}, Exception("driver wrapper"))
    driver_error.orig.__cause__ = _win121()
    assert is_transient_database_disconnect(driver_error)


def test_driver_sqlstate_overrides_a_generic_disconnect_flag():
    error = DBAPIError("SELECT 1", {}, asyncpg.InvalidPasswordError("bad"), connection_invalidated=True)
    assert not is_transient_database_disconnect(error)


def test_exception_cycles_do_not_loop():
    error = RuntimeError("unknown")
    error.__cause__ = error
    assert not is_transient_database_disconnect(error)


def test_os_disconnect_in_implicit_exception_context_is_recognized():
    error = DBAPIError("SELECT 1", {}, Exception("driver wrapper"))
    error.__context__ = _win121()
    assert is_transient_database_disconnect(error)


async def test_success_runs_steps_in_order_without_cleanup_or_delay(monkeypatch):
    events = []

    async def first():
        events.append("first")

    async def second():
        events.append("second")

    dispose = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)
    await run_database_startup_steps([first, second], dispose=dispose)
    assert events == ["first", "second"]
    dispose.assert_not_awaited()
    sleep.assert_not_awaited()


async def test_recovers_only_failed_step_without_replaying_successful_steps(monkeypatch):
    first = AsyncMock()
    failed_step = AsyncMock(side_effect=[_win121(), asyncpg.ConnectionDoesNotExistError(), None])
    last = AsyncMock()
    dispose = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)

    await run_database_startup_steps([first, failed_step, last], dispose=dispose)

    first.assert_awaited_once()
    assert failed_step.await_count == 3
    last.assert_awaited_once()
    assert dispose.await_count == 2
    assert sleep.await_args_list == [call(0.25), call(0.5)]


async def test_exhaustion_disposes_each_failed_pool_and_raises_original(monkeypatch):
    original = asyncpg.ConnectionDoesNotExistError("original error")
    step = AsyncMock(side_effect=original)
    next_step = AsyncMock()
    dispose = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)

    with pytest.raises(asyncpg.ConnectionDoesNotExistError) as caught:
        await run_database_startup_steps([step, next_step], dispose=dispose)

    assert caught.value is original
    assert step.await_count == 3
    assert dispose.await_count == 3
    assert sleep.await_args_list == [call(0.25), call(0.5)]
    next_step.assert_not_awaited()


@pytest.mark.parametrize("original", [asyncpg.InvalidPasswordError(), ProgrammingError("bad SQL", {}, Exception()), RuntimeError()])
async def test_fatal_failure_propagates_immediately(original, monkeypatch):
    step = AsyncMock(side_effect=original)
    dispose = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)
    with pytest.raises(type(original)) as caught:
        await run_database_startup_steps([step], dispose=dispose)
    assert caught.value is original
    step.assert_awaited_once()
    dispose.assert_not_awaited()
    sleep.assert_not_awaited()


async def test_cancellation_is_never_retried(monkeypatch):
    step = AsyncMock(side_effect=asyncio.CancelledError())
    dispose = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_database_startup_steps([step], dispose=dispose)
    step.assert_awaited_once()
    dispose.assert_not_awaited()
    sleep.assert_not_awaited()


async def test_failed_pool_is_disposed_before_waiting_and_retrying(monkeypatch):
    events = []

    async def step():
        events.append("step")
        if events.count("step") == 1:
            raise _win121()

    async def dispose():
        events.append("dispose")

    async def sleep(_delay):
        events.append("sleep")

    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)
    await run_database_startup_steps([step], dispose=dispose)
    assert events == ["step", "dispose", "sleep", "step"]


async def test_cleanup_failure_stops_instead_of_reusing_a_broken_pool(monkeypatch):
    original = _win121()
    cleanup_error = RuntimeError("pool cleanup failed")
    step = AsyncMock(side_effect=original)
    dispose = AsyncMock(side_effect=cleanup_error)
    sleep = AsyncMock()
    monkeypatch.setattr("app.startup_database_retry.asyncio.sleep", sleep)
    with pytest.raises(RuntimeError) as caught:
        await run_database_startup_steps([step], dispose=dispose)
    assert caught.value is cleanup_error
    assert cleanup_error.__context__ is original
    step.assert_awaited_once()
    dispose.assert_awaited_once()
    sleep.assert_not_awaited()
