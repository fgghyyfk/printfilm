"""市场媒体循环独立托管，沿用主业务随 API 启停和看门狗恢复的方式。"""
import asyncio
import logging

from .config import is_marketplace_enabled
from .worker import run_loop

logger = logging.getLogger(__name__)
_worker: asyncio.Task | None = None
_watchdog: asyncio.Task | None = None


async def _supervise():
    global _worker
    while True:
        if _worker is None or _worker.done():
            if _worker is not None and not _worker.cancelled():
                error = _worker.exception()
                if error:
                    logger.error("市场 Worker 异常退出，自动恢复", exc_info=(type(error), error, error.__traceback__))
            _worker = asyncio.create_task(run_loop(), name="marketplace-media-worker")
        await asyncio.sleep(2)


async def start_marketplace_runtime():
    global _watchdog
    if not is_marketplace_enabled():
        return
    if _watchdog is not None and not _watchdog.done():
        return
    _watchdog = asyncio.create_task(_supervise(), name="marketplace-worker-watchdog")


async def stop_marketplace_runtime():
    global _worker, _watchdog
    # 先停看门狗，避免关闭期间把 Worker 再次拉起。
    if _watchdog is not None:
        _watchdog.cancel()
        await asyncio.gather(_watchdog, return_exceptions=True)
        _watchdog = None
    if _worker is not None:
        _worker.cancel()
        await asyncio.gather(_worker, return_exceptions=True)
        _worker = None
