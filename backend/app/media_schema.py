"""Idempotent additive media billing migration; never rewrites historical bills."""
from sqlalchemy import text


MEDIA_COLUMNS = {
    "projects": {"image_resolution": "VARCHAR(16) NOT NULL DEFAULT ''", "video_resolution": "VARCHAR(16) NOT NULL DEFAULT ''"},
    "task_runs": {"billing_snapshot": "JSON", "media_provider_tasks": "JSON"},
    "usage_events": {"billing_detail": "JSON", "delivery_key": "VARCHAR(192)", "cost_status": "VARCHAR(16)"},
}


class MediaSchemaNotReady(RuntimeError):
    """仅携带缺失结构名称，便于发布命令报错且不泄漏数据库连接信息。"""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("媒体计费结构未就绪：" + "、".join(missing))


async def apply_media_schema_patches(conn):
    """仅补齐缺失的媒体列和唯一索引，不改写历史数据。"""
    rows = await conn.execute(text("SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name IN ('projects', 'task_runs', 'usage_events')"))
    present = set(rows.fetchall())
    for table, definitions in MEDIA_COLUMNS.items():
        for column, definition in definitions.items():
            if (table, column) not in present:
                # Identifiers and types come only from the static whitelist above.
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"))
    if await conn.scalar(text("SELECT to_regclass('uq_usage_delivery_key')")) is None:
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_delivery_key ON usage_events (delivery_key) WHERE delivery_key IS NOT NULL"))


async def assert_media_schema_ready(conn) -> None:
    """只读检查新代码依赖的表、列和有效唯一索引；缺失时阻止发布。"""
    rows = await conn.execute(text("SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name IN "
        "('projects', 'task_runs', 'usage_events', 'media_price_versions')"))
    present = set(rows.fetchall())
    required = {**MEDIA_COLUMNS, "media_price_versions": dict.fromkeys(("id", "prices", "created_by", "created_at"))}
    missing = [f"{table}.{column}" for table, columns in required.items() for column in columns if (table, column) not in present]
    index_ready = await conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_index i "
        "JOIN pg_class idx ON idx.oid = i.indexrelid "
        "JOIN pg_class tab ON tab.oid = i.indrelid "
        "JOIN pg_namespace ns ON ns.oid = tab.relnamespace "
        "JOIN pg_attribute col ON col.attrelid = tab.oid AND col.attnum = i.indkey[0] "
        "WHERE ns.nspname = current_schema() AND tab.relname = 'usage_events' "
        "AND idx.relname = 'uq_usage_delivery_key' AND col.attname = 'delivery_key' "
        "AND i.indisunique AND i.indisvalid AND i.indnkeyatts = 1 AND i.indexprs IS NULL)"
    ))
    if not index_ready:
        missing.append("usage_events.uq_usage_delivery_key")
    if missing:
        raise MediaSchemaNotReady(missing)
