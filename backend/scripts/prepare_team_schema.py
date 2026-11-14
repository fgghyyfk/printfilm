"""为关闭启动建表的现有开发环境显式执行幂等增量迁移。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys


async def prepare() -> None:
    """按项目既有顺序创建缺失表、补列和索引，不启动生成任务或种子流程。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings
    from app.database import init_db
    from app.main import _apply_schema_patches

    # 迁移连接独立于应用池，关闭 JIT 并限制语句/空闲事务，避免断连后长期持 DDL 锁。
    migration_engine = create_async_engine(get_settings().database_url, connect_args={
        "server_settings": {
            "application_name": "printfilm-team-schema",
            "jit": "off",
            "statement_timeout": "45000",
            "idle_in_transaction_session_timeout": "45000",
        },
    })
    try:
        await asyncio.wait_for(init_db(engine_override=migration_engine), timeout=60)
        await asyncio.wait_for(_apply_schema_patches(engine_override=migration_engine), timeout=60)
    finally:
        await migration_engine.dispose()


def main() -> int:
    """使用 backend/.env 的现有连接配置，错误只报告类型，不输出凭据。"""
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    print("正在准备团队模式数据库结构……", flush=True)
    try:
        asyncio.run(prepare())
    except Exception as error:
        print(f"数据库准备未完成（{type(error).__name__}），请检查连接后重试。", file=sys.stderr)
        return 1
    print("团队模式数据库结构已就绪。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
