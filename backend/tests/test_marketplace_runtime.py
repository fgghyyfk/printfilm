"""媒体生命周期测试不连接 OSS，不启动真实转码。"""
import asyncio
import sys

import pytest

from app.marketplace import runtime
from app.config import get_settings


def test_media_tools_fall_back_to_environment_paths(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app import config
    from app.marketplace import worker
    executable = tmp_path / 'ffprobe.exe'
    executable.touch()
    monkeypatch.setattr(worker.shutil, 'which', lambda value: None)
    monkeypatch.setattr(config, 'Settings', lambda: SimpleNamespace(ffprobe_path=str(executable)))
    assert worker.resolve_tool('ffprobe') == str(executable)
    explicit = str(tmp_path / 'custom.exe')
    assert worker.resolve_tool(explicit) == explicit


async def test_media_command_selector_loop_fallback(monkeypatch):
    from app.marketplace import worker
    async def unsupported(*args, **kwargs):
        raise NotImplementedError
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', unsupported)
    output = await worker.command(sys.executable, '-c', 'print("selector-compatible")', timeout=10)
    assert output.strip() == b'selector-compatible'


async def test_media_thread_command_timeout_and_cancellation(monkeypatch):
    from app.marketplace import worker
    original = worker.subprocess.Popen
    processes = []
    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(worker.subprocess, 'Popen', capture)
    args = (sys.executable, '-c', 'import time; time.sleep(10)')
    with pytest.raises(TimeoutError):
        await worker.threaded_command(args, .1)
    assert processes[-1].poll() is not None
    task = asyncio.create_task(worker.threaded_command(args, 20))
    while len(processes) < 2:
        await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[-1].poll() is not None


async def test_market_runtime_disabled_does_not_start(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketplace_enabled", False)
    await runtime.start_marketplace_runtime()
    assert runtime._watchdog is None and runtime._worker is None


async def test_market_runtime_idempotent_and_clean_shutdown(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketplace_enabled", True)
    started, stopped = asyncio.Event(), asyncio.Event()
    async def loop():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(runtime, "run_loop", loop)
    try:
        await runtime.start_marketplace_runtime()
        await asyncio.wait_for(started.wait(), 1)
        original = runtime._worker
        await runtime.start_marketplace_runtime()
        assert runtime._worker is original
    finally:
        await runtime.stop_marketplace_runtime()
    assert stopped.is_set()
    assert runtime._worker is None and runtime._watchdog is None
    await runtime.stop_marketplace_runtime()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_market_runtime_recovers_failed_or_cancelled_loop(monkeypatch, cancelled):
    monkeypatch.setattr(get_settings(), "marketplace_enabled", True)
    started, restarted = asyncio.Event(), asyncio.Event()
    calls = 0
    async def loop():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            if cancelled:
                raise asyncio.CancelledError
            raise RuntimeError("test unexpected exit")
        restarted.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(runtime, "run_loop", loop)
    try:
        await runtime.start_marketplace_runtime()
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(restarted.wait(), 4)
        assert calls == 2 and not runtime._worker.done()
    finally:
        await runtime.stop_marketplace_runtime()
