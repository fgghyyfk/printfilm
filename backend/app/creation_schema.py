"""Scoped additive migration for conversational short-video creation."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


PROJECT_COLUMNS = {
    "creation_session_id": "INTEGER",
    "target_duration_sec": "INTEGER DEFAULT 60",
}
PROJECT_INDEX = "ix_projects_creation_session_id"


class CreationSchemaNotReady(RuntimeError):
    """Only schema identifiers are exposed; never connection or business data."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("创作会话结构未就绪：" + "、".join(missing))


def _creation_tables():
    # Register FK targets, but never create or alter these prerequisite tables.
    from app import models, models_tasks  # noqa: F401
    from app.models_creation import CreationMessage, CreationRun, CreationSession

    return (CreationSession.__table__, CreationMessage.__table__, CreationRun.__table__)


async def apply_creation_schema_patches(conn: AsyncConnection) -> None:
    """Create only creation tables and the two project columns; preserve all rows."""
    if conn.dialect.name == "postgresql":
        # Serialize concurrent invocations before inspecting/creating structures.
        await conn.execute(text("SELECT pg_advisory_xact_lock(82632024, 10424)"))

    present = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    prerequisites = {"users", "templates", "projects", "task_runs"}
    if missing := sorted(prerequisites - present):
        raise CreationSchemaNotReady(missing)

    columns = await conn.run_sync(
        lambda sync: {column["name"] for column in inspect(sync).get_columns("projects")}
    )
    for name, definition in PROJECT_COLUMNS.items():
        if name not in columns:
            # Identifiers/types are exclusively from the static whitelist above.
            await conn.execute(text(f"ALTER TABLE projects ADD COLUMN {name} {definition}"))
    await conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS {PROJECT_INDEX} ON projects (creation_session_id)"
    ))

    for table in _creation_tables():
        await conn.run_sync(lambda sync, table=table: table.create(sync, checkfirst=True))


async def assert_creation_schema_ready(conn: AsyncConnection) -> None:
    """Read-only verification of every column required by the creation models."""
    expected = {"projects": set(PROJECT_COLUMNS)}
    for table in _creation_tables():
        expected[table.name] = set(table.columns.keys())

    def inspect_missing(sync):
        inspector = inspect(sync)
        tables = set(inspector.get_table_names())
        missing = []
        for table, columns in expected.items():
            if table not in tables:
                missing.append(table)
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            missing.extend(f"{table}.{column}" for column in sorted(columns - present))
        if "projects" in tables:
            indexes = inspector.get_indexes("projects")
            if not any(index["name"] == PROJECT_INDEX and index["column_names"] == ["creation_session_id"]
                       for index in indexes):
                missing.append(f"projects.{PROJECT_INDEX}")
        return missing

    missing = await conn.run_sync(inspect_missing)
    if missing:
        raise CreationSchemaNotReady(missing)
