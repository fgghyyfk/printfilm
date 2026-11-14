"""Exercise scoped migration DDL against an isolated in-memory database."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import config, creation_schema
from scripts import prepare_creation_schema as cli


@pytest.mark.asyncio
@pytest.mark.parametrize("has_duration", [False, True])
async def test_additive_upgrade_is_repeatable_and_preserves_legacy_data(has_duration):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            for table in ("users", "task_runs"):
                await conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE templates (id VARCHAR(64) PRIMARY KEY)"))
            duration = ", target_duration_sec INTEGER DEFAULT 60" if has_duration else ""
            await conn.execute(text(f"CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT{duration})"))
            await conn.execute(text("CREATE TABLE unrelated_records (id INTEGER PRIMARY KEY, payload TEXT)"))
            await conn.execute(text("INSERT INTO unrelated_records VALUES (1, 'keep')"))
            await conn.execute(text("INSERT INTO projects (id, title) VALUES (1, 'legacy')"))
            if has_duration:
                await conn.execute(text("UPDATE projects SET target_duration_sec = 77"))

            with pytest.raises(creation_schema.CreationSchemaNotReady):
                await creation_schema.assert_creation_schema_ready(conn)
            before = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
            for _ in range(2):
                await creation_schema.apply_creation_schema_patches(conn)
                await creation_schema.assert_creation_schema_ready(conn)

            after = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
            assert after - before == {"creation_sessions", "creation_messages", "creation_runs"}
            row = (await conn.execute(text("SELECT title, creation_session_id, target_duration_sec FROM projects"))).one()
            assert tuple(row) == ("legacy", None, 77 if has_duration else 60)
            assert await conn.scalar(text("SELECT payload FROM unrelated_records")) == "keep"
            assert await conn.scalar(text("SELECT COUNT(*) FROM creation_sessions")) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_prerequisites_fail_before_altering_schema():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            with pytest.raises(creation_schema.CreationSchemaNotReady) as failure:
                await creation_schema.apply_creation_schema_patches(conn)
            assert failure.value.missing == ["projects", "task_runs", "templates", "users"]
            assert await conn.run_sync(lambda sync: inspect(sync).get_table_names()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("check_only", [False, True])
async def test_cli_check_is_read_only_and_uses_dedicated_transaction(monkeypatch, check_only):
    from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

    conn = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def begin():
        yield conn

    engine = SimpleNamespace(begin=begin, dispose=AsyncMock())
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(database_url="postgresql+asyncpg://test.invalid/test"))
    create = Mock(return_value=engine)
    monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", create)
    patches, verify = AsyncMock(), AsyncMock()
    monkeypatch.setattr(creation_schema, "apply_creation_schema_patches", patches)
    monkeypatch.setattr(creation_schema, "assert_creation_schema_ready", verify)
    await cli.prepare(check_only=check_only)
    assert patches.await_count == int(not check_only)
    verify.assert_awaited_once_with(conn)
    if check_only:
        assert str(conn.execute.await_args.args[0]) == "SET TRANSACTION READ ONLY"
    assert create.call_args.kwargs["connect_args"]["server_settings"]["lock_timeout"] == "10000"
    engine.dispose.assert_awaited_once()


def test_cli_errors_do_not_expose_connection_credentials(monkeypatch, capsys):
    monkeypatch.setattr(cli, "prepare", AsyncMock(side_effect=RuntimeError("postgresql://secret@test.invalid/db")))
    assert cli.main(["--check"]) == 1
    assert "secret" not in capsys.readouterr().err
