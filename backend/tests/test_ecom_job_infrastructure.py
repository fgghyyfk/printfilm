"""电商任务的估价、注册与老库补丁顺序回归。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services.billing import estimates
from app.services.tasks.handlers import get_task_handler


# 2K 与 4K 都按图像目录估价，不能误走默认文本估价。
@pytest.mark.asyncio
@pytest.mark.parametrize("clarity,price", [("2k", 23), ("4k", 47)])
async def test_ecom_estimate_uses_clarity(monkeypatch, clarity, price):
    # 捕获目录价格的清晰度，模拟两档差异。
    def image_price(settings, *, model, size):
        assert size.lower() == clarity
        return {"2k": 23, "4k": 47}[size.lower()]

    monkeypatch.setattr(estimates, "charge_fen_official_image", image_price)
    task = TaskRun(domain="studio", task_type="ecom_job_image", payload={"clarity": clarity})
    assert await estimates.estimate_task_fen(None, task, get_settings()) == price


# 通用电商图任务必须能被任务平台调度。
def test_ecom_handler_registered():
    handler = get_task_handler("studio", "ecom_job_image")
    assert handler is not None
    assert callable(handler.executor)


# 新增列必须先于引用该列的唯一索引创建，兼容老库启动。
@pytest.mark.asyncio
async def test_legacy_schema_column_precedes_index(db_session, monkeypatch):
    from sqlalchemy import text
    from app import main

    connection = await db_session.connection()
    # 仅在 db_session 的测试库回滚事务内模拟老库缺列；不触及业务库。
    await connection.execute(text("ALTER TABLE projects DROP COLUMN ecom_set_id"))

    # 将启动补丁绑定到当前测试事务，验证实际 PostgreSQL DDL 顺序。
    @asynccontextmanager
    async def begin_in_test():
        yield connection

    monkeypatch.setattr(main, "engine", SimpleNamespace(begin=begin_in_test))
    await main._apply_schema_patches()
    assert "ecom_set_id" in await main._pg_columns(connection, "projects")
    assert await connection.scalar(text("SELECT to_regclass('uq_projects_daihuo_request')"))


# 主应用必须对外挂载整套接口，不能只在独立测试应用里可用。
def test_main_mounts_all_ecom_routes():
    from app.main import app

    paths = {(method, route.path) for route in app.routes for method in getattr(route, "methods", [])}
    assert {
        ("GET", "/api/tools/ecom-jobs/options"),
        ("POST", "/api/tools/ecom-jobs/{kind}"),
        ("GET", "/api/tools/ecom-jobs"),
        ("GET", "/api/tools/ecom-jobs/{job_id}"),
        ("POST", "/api/tools/ecom-jobs/{job_id}/items/{idx}/retry"),
        ("GET", "/api/tools/ecom-jobs/{job_id}/download"),
    } <= paths


# mock 电商入口的估价不得请求外网价目，即使启用计费也只使用本地目录。
@pytest.mark.asyncio
async def test_ecom_mock_estimate_stays_offline(monkeypatch):
    settings = get_settings().model_copy(update={"ark_mock": True})

    # 覆盖全局测试 fixture 的空价目替身，确保真实调用路径确实绕过网络。
    async def forbid_rates(*args):
        raise AssertionError("mock 不能拉远程价目")

    monkeypatch.setattr(estimates, "ensure_official_rates", forbid_rates)
    task = TaskRun(domain="studio", task_type="ecom_job_image", payload={"clarity": "2k"})
    assert await estimates.estimate_task_fen(None, task, settings) > 0
