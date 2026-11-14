"""发布检查在迁移前后执行，检查模式不建表或补列。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import config, media_schema
from scripts import prepare_media_schema as cli


@pytest.mark.asyncio
@pytest.mark.parametrize("check_only", [True, False])
async def test_check_mode_is_read_only_and_prepare_verifies_before_commit(monkeypatch, check_only):
    from sqlalchemy.ext import asyncio as sqlalchemy_asyncio
    from app.models_settings import MediaPriceVersion
    conn = SimpleNamespace(run_sync=AsyncMock(), execute=AsyncMock())
    @asynccontextmanager
    async def begin():
        yield conn
    engine = SimpleNamespace(begin=begin, dispose=AsyncMock())
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(database_url="postgresql+asyncpg://test.invalid/media_test"))
    monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", Mock(return_value=engine))
    patches = AsyncMock()
    verify = AsyncMock()
    monkeypatch.setattr(media_schema, "apply_media_schema_patches", patches)
    monkeypatch.setattr(media_schema, "assert_media_schema_ready", verify, raising=False)
    await cli.prepare(check_only=check_only)
    verify.assert_awaited_once_with(conn)
    assert patches.await_count == int(not check_only)
    assert conn.run_sync.await_count == int(not check_only)
    if check_only:
        assert str(conn.execute.await_args.args[0]) == "SET TRANSACTION READ ONLY"
    else:
        create_table = Mock()
        sync = object()
        monkeypatch.setattr(MediaPriceVersion.__table__, "create", create_table)
        conn.run_sync.await_args.args[0](sync)
        create_table.assert_called_once_with(sync, checkfirst=True)
    engine.dispose.assert_awaited_once()


def test_cli_check_failure_returns_nonzero_without_printing_credentials(monkeypatch, capsys):
    prepare = AsyncMock(side_effect=RuntimeError("postgresql://secret-password@test.invalid/db"))
    monkeypatch.setattr(cli, "prepare", prepare)
    assert cli.main(["--check"]) == 1
    output = capsys.readouterr()
    assert "secret-password" not in output.err
    prepare.assert_awaited_once_with(check_only=True)


def test_cli_check_reports_missing_schema_and_blocks_release(monkeypatch, capsys):
    prepare = AsyncMock(side_effect=media_schema.MediaSchemaNotReady(["task_runs.billing_snapshot"]))
    monkeypatch.setattr(cli, "prepare", prepare)
    assert cli.main(["--check"]) == 1
    assert "task_runs.billing_snapshot" in capsys.readouterr().err
