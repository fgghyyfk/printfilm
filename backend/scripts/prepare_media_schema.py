"""Explicit additive migration for installations with database bootstrap disabled."""
from __future__ import annotations

import asyncio
import argparse
import os
from pathlib import Path
import sys


async def prepare(*, check_only: bool = False) -> None:
    """迁移并校验，或在只读事务中检查当前目标库；失败自动回滚。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import get_settings
    from app.models_settings import MediaPriceVersion
    from app.media_schema import apply_media_schema_patches, assert_media_schema_ready

    engine = create_async_engine(get_settings().database_url, connect_args={"server_settings": {
        "application_name": "printfilm-media-schema", "statement_timeout": "45000",
        "idle_in_transaction_session_timeout": "45000", "lock_timeout": "10000",
    }})
    try:
        async with engine.begin() as conn:
            if check_only:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            else:
                await conn.run_sync(lambda sync: MediaPriceVersion.__table__.create(sync, checkfirst=True))
                await apply_media_schema_patches(conn)
            await assert_media_schema_ready(conn)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """缺少结构时返回非零供发布流程中止；不打印 DSN、凭据或业务数据。"""
    parser = argparse.ArgumentParser(description="媒体计费增量迁移与发布前结构检查")
    parser.add_argument("--check", action="store_true", help="只读校验结构，不执行迁移；未就绪返回 1")
    args = parser.parse_args(argv)
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    from app.media_schema import MediaSchemaNotReady
    try:
        asyncio.run(prepare(check_only=args.check))
    except MediaSchemaNotReady as error:
        print(f"{error}。请先执行媒体计费迁移，停止本次发布。", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"媒体计费结构准备失败（{type(error).__name__}），请检查数据库连接。", file=sys.stderr)
        return 1
    print("媒体计费结构检查通过。" if args.check else "媒体计费结构已就绪；尚未发布价格时，新任务继续沿用原计费。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
