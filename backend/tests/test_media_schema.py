"""Additive migration skips existing columns and never rewrites billing data."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.media_schema import apply_media_schema_patches
from app import media_schema


@pytest.mark.asyncio
async def test_existing_media_schema_does_not_acquire_ddl_locks():
    columns = [("projects", name) for name in ("image_resolution", "video_resolution")]
    columns += [("task_runs", name) for name in ("billing_snapshot", "media_provider_tasks")]
    columns += [("usage_events", name) for name in ("billing_detail", "delivery_key", "cost_status")]
    result = Mock()
    result.fetchall.return_value = columns
    conn = SimpleNamespace(execute=AsyncMock(return_value=result), scalar=AsyncMock(return_value="uq_usage_delivery_key"))
    await apply_media_schema_patches(conn)
    conn.execute.assert_awaited_once()
    assert "information_schema.columns" in str(conn.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_missing_media_columns_are_added_without_rewriting_history():
    result = Mock()
    result.fetchall.return_value = []
    conn = SimpleNamespace(execute=AsyncMock(return_value=result), scalar=AsyncMock(return_value=None))
    await apply_media_schema_patches(conn)
    commands = [str(call.args[0]) for call in conn.execute.await_args_list][1:]
    assert len(commands) == 8
    assert all(command.startswith("ALTER TABLE ") for command in commands[:-1])
    assert commands[-1].startswith("CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_delivery_key")
    assert not any("UPDATE " in command or "DELETE " in command for command in commands)


def ready_columns():
    """模拟已经完成图视频计费迁移的数据库元数据。"""
    return [("projects", name) for name in ("image_resolution", "video_resolution")] + [
        ("task_runs", "billing_snapshot"), ("task_runs", "media_provider_tasks"),
        ("usage_events", "billing_detail"), ("usage_events", "delivery_key"),
        ("usage_events", "cost_status"), ("media_price_versions", "id"),
        ("media_price_versions", "prices"), ("media_price_versions", "created_by"),
        ("media_price_versions", "created_at"),
    ]


@pytest.mark.asyncio
async def test_ready_schema_check_only_reads_metadata():
    result = Mock()
    result.fetchall.return_value = ready_columns()
    conn = SimpleNamespace(execute=AsyncMock(return_value=result), scalar=AsyncMock(return_value=True))
    await media_schema.assert_media_schema_ready(conn)
    conn.execute.assert_awaited_once()
    assert str(conn.execute.await_args.args[0]).startswith("SELECT ")
    assert str(conn.scalar.await_args.args[0]).startswith("SELECT ")


@pytest.mark.asyncio
async def test_missing_column_or_index_blocks_release_with_actionable_names():
    result = Mock()
    result.fetchall.return_value = [column for column in ready_columns() if column != ("task_runs", "billing_snapshot")]
    conn = SimpleNamespace(execute=AsyncMock(return_value=result), scalar=AsyncMock(return_value=False))
    with pytest.raises(RuntimeError) as caught:
        await media_schema.assert_media_schema_ready(conn)
    assert caught.value.missing == ["task_runs.billing_snapshot", "usage_events.uq_usage_delivery_key"]


@pytest.mark.asyncio
async def test_missing_price_table_blocks_release_before_prices_are_published():
    result = Mock()
    result.fetchall.return_value = [column for column in ready_columns() if column[0] != "media_price_versions"]
    conn = SimpleNamespace(execute=AsyncMock(return_value=result), scalar=AsyncMock(return_value=True))
    with pytest.raises(RuntimeError) as caught:
        await media_schema.assert_media_schema_ready(conn)
    assert "media_price_versions.prices" in caught.value.missing
