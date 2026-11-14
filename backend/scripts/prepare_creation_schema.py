"""Explicit, scoped creation-schema upgrade when database bootstrap is disabled."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys


async def prepare(*, check_only: bool = False) -> None:
    """Use one transaction; a failed migration or validation rolls back the DDL."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings
    from app.creation_schema import apply_creation_schema_patches, assert_creation_schema_ready

    engine = create_async_engine(get_settings().database_url, connect_args={"server_settings": {
        "application_name": "printfilm-creation-schema",
        "jit": "off",
        "statement_timeout": "45000",
        "idle_in_transaction_session_timeout": "45000",
        "lock_timeout": "10000",
    }})
    try:
        async with engine.begin() as conn:
            if check_only:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            else:
                await apply_creation_schema_patches(conn)
            await assert_creation_schema_ready(conn)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创作会话增量迁移（不执行种子数据或其他业务迁移）")
    parser.add_argument("--check", action="store_true", help="只读校验结构；缺失时返回 1")
    args = parser.parse_args(argv)
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    from app.creation_schema import CreationSchemaNotReady

    try:
        asyncio.run(prepare(check_only=args.check))
    except CreationSchemaNotReady as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(f"创作会话结构准备失败（{type(error).__name__}），请检查数据库连接后重试。", file=sys.stderr)
        return 1
    print("创作会话结构检查通过。" if args.check else "创作会话结构已就绪，请重启后端以加载新接口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
