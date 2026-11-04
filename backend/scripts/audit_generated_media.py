"""默认只审计；显式--apply才逐文件回收无引用、无项目归属的老孤儿。"""
import argparse
import asyncio
import json
from pathlib import Path


async def run(args):
    from app.database import AsyncSessionLocal
    from app.services.generated_media_gc import audit_generated_orphans
    from app.services.storage import GENERATED_ROOT

    async with AsyncSessionLocal() as db:
        return await audit_generated_orphans(db, root=Path(args.root) if args.root else GENERATED_ROOT,
                                             retention_days=args.days, apply=args.apply)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit unreferenced generated pN files (dry-run by default)")
    parser.add_argument("--root", help="显式生成目录；默认backend/static/generated")
    parser.add_argument("--days", type=int, default=30, help="mtime与ctime均超过此天数才考虑，至少1天")
    parser.add_argument("--apply", action="store_true", help="启用逐文件删除；默认只报告")
    result = asyncio.run(run(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
