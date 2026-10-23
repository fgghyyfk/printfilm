"""任务平台启动回滚、关闭清理与健康摘要的回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.tasks import runtime


@pytest.fixture
def components(monkeypatch):
    """替换组件生命周期，避免测试连接数据库或启动后台任务。"""
    mocks = {}
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(task_runtime_enabled=True))
    monkeypatch.setattr(runtime, "scheduler_diagnostics", lambda: {
        "scheduler_success_age_sec": 0.0, "scheduler_consecutive_failures": 0,
    })
    for name in ("scheduler", "poller", "watchdog"):
        for action in ("start", "stop"):
            key = f"{action}_{name}"
            mocks[key] = AsyncMock()
            monkeypatch.setattr(runtime, key, mocks[key])
    return mocks


@pytest.mark.asyncio
async def test_disabled_runtime_does_not_start_components(components, monkeypatch):
    """共享数据库的本地 API 可以关闭任务消费，同时继续提供健康检查。"""
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(task_runtime_enabled=False))
    await runtime.start_task_runtime()
    for name in ("scheduler", "poller", "watchdog"):
        components[f"start_{name}"].assert_not_awaited()
    summary = runtime.runtime_summary()
    assert summary["enabled"] is False
    assert summary["healthy"] is True
    assert [summary[name] for name in ("scheduler", "poller", "watchdog")] == ["disabled"] * 3


@pytest.mark.asyncio
async def test_missing_enabled_setting_preserves_existing_startup(components, monkeypatch):
    """旧配置替身没有开关字段时，仍按启用处理。"""
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())
    await runtime.start_task_runtime()
    components["start_scheduler"].assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_start_keeps_components_running(components):
    """启动成功后不应触发回滚。"""
    await runtime.start_task_runtime()
    for name in ("scheduler", "poller", "watchdog"):
        components[f"start_{name}"].assert_awaited_once()
        components[f"stop_{name}"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("启动失败"), asyncio.CancelledError()])
@pytest.mark.parametrize("failed_component", ["scheduler", "poller", "watchdog"])
async def test_start_failure_rolls_back_started_components(components, failure, failed_component):
    """启动异常或取消时，仅清理此前成功启动的组件。"""
    names = ("scheduler", "poller", "watchdog")
    components[f"start_{failed_component}"].side_effect = failure
    with pytest.raises(type(failure)):
        await runtime.start_task_runtime()
    for index, name in enumerate(names):
        if index < names.index(failed_component):
            components[f"stop_{name}"].assert_awaited_once()
        else:
            components[f"stop_{name}"].assert_not_awaited()
        if index > names.index(failed_component):
            components[f"start_{name}"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_component", ["watchdog", "poller", "scheduler"])
@pytest.mark.parametrize("failure", [RuntimeError("关闭失败"), asyncio.CancelledError()])
async def test_stop_failure_still_cleans_up_remaining_components(components, failed_component, failure):
    """关闭报错或取消不能跳过其余组件的清理。"""
    components[f"stop_{failed_component}"].side_effect = failure
    with pytest.raises(type(failure)):
        await runtime.stop_task_runtime()
    for name in ("watchdog", "poller", "scheduler"):
        components[f"stop_{name}"].assert_awaited_once()


@pytest.mark.parametrize("scheduler_running", [True, False])
@pytest.mark.parametrize("poller_running", [True, False])
@pytest.mark.parametrize("watchdog_running", [True, False])
@pytest.mark.parametrize("scheduler_stale,poller_stale", [(False, False), (True, False), (False, True), (True, True)])
def test_health_requires_live_components_and_fresh_heartbeats(
    monkeypatch, scheduler_running, poller_running, watchdog_running, scheduler_stale, poller_stale,
):
    """运行状态与心跳都正常时，健康摘要才能报告 healthy。"""
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(task_runtime_enabled=True))
    monkeypatch.setattr(runtime, "scheduler_diagnostics", lambda: {
        "scheduler_success_age_sec": 1.0, "scheduler_consecutive_failures": 0,
    })
    for name, running in (("scheduler", scheduler_running), ("poller", poller_running), ("watchdog", watchdog_running)):
        monkeypatch.setattr(runtime, f"{name}_status", lambda running=running: "running" if running else "stopped")
    monkeypatch.setattr(runtime, "scheduler_tick_stale", lambda: scheduler_stale)
    monkeypatch.setattr(runtime, "poller_tick_stale", lambda: poller_stale)
    monkeypatch.setattr(runtime, "scheduler_tick_age_sec", lambda: 120.0 if scheduler_stale else 1.0)
    monkeypatch.setattr(runtime, "poller_tick_age_sec", lambda: 120.0 if poller_stale else 1.0)
    monkeypatch.setattr(runtime, "running_count", lambda: 2)
    summary = runtime.runtime_summary()
    assert summary["healthy"] is (
        scheduler_running and poller_running and watchdog_running and not scheduler_stale and not poller_stale
    )
    assert summary["scheduler_running_jobs"] == 2
