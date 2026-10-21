"""管理端任务事件必须有界加载，全部使用内存替身而不连接数据库。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.api.admin import tasks as api
from app.models_tasks import TaskRun
from app.services.tasks import admin_events, service


class _Result:
    """兼容事件与任务查询使用的结果接口。"""

    def __init__(self, rows=(), scalar=None):
        """保存预设行和标量。"""
        self.rows = list(rows)
        self.scalar = scalar

    def all(self):
        """返回预设查询行。"""
        return self.rows

    def unique(self):
        """兼容任务列表结果。"""
        return self

    def scalars(self):
        """兼容实体列表结果。"""
        return self

    def scalar_one(self):
        """返回聚合标量。"""
        return self.scalar

    def scalar_one_or_none(self):
        """返回存在性检查或单条任务。"""
        return self.scalar


def _sql(statement):
    """生成 PostgreSQL SQL，检查数据库端过滤、排序与上限。"""
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _loader_strategies(statement):
    """提取 ORM 加载策略，防止重新加入全量事件 selectinload。"""
    return [(str(context.path), context.strategy) for option in statement._with_options for context in option.context]


@pytest.mark.asyncio
async def test_admin_task_queries_never_eager_load_event_history():
    """列表和详情只加载步骤与目标，事件关系禁止意外懒加载。"""
    task = SimpleNamespace(id=6807)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(scalar=task), _Result(scalar=1), _Result([(task, "x@example.test")])]))
    assert await service.get_task_admin(db, 6807) is task
    await service.list_tasks_admin(db, page=1, page_size=20)
    for call in (db.execute.call_args_list[0], db.execute.call_args_list[2]):
        event_strategies = [strategy for path, strategy in _loader_strategies(call.args[0]) if "TaskRun.events" in path]
        assert event_strategies == [(("lazy", "raise"),)]


@pytest.mark.asyncio
async def test_list_summaries_query_only_bounded_upstream_events():
    """一页任务批量读取计数和最近上游事件，绝不读取普通事件实体。"""
    upstream = SimpleNamespace(id=90000, task_id=6807, event_type="upstream.request.failed")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result([(6807, 64071, 2)]), _Result([upstream])]))
    result = await admin_events.load_admin_event_summaries(db, [6807, 6808])
    assert result[6807].events == []
    assert result[6807].events_total == 64071
    assert result[6807].upstream_events == [upstream]
    assert result[6807].upstream_events_total == 2
    assert result[6808].events_total == 0
    assert db.execute.await_count == 2
    sql = _sql(db.execute.call_args_list[1].args[0])
    assert "row_number() OVER (PARTITION BY task_events.task_id ORDER BY task_events.id DESC)" in sql
    assert "upstream.request." in sql and "<= 20" in sql


@pytest.mark.asyncio
async def test_detail_keeps_recent_events_and_independent_upstream_history():
    """海量激活日志不能挤掉早先真实上游请求。"""
    upstream = SimpleNamespace(id=1, task_id=6807, event_type="upstream.request.started")
    recent = SimpleNamespace(id=64071, task_id=6807, event_type="task.failed")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result([(6807, 64071, 2)]), _Result([upstream]), _Result([recent])]))
    result = (await admin_events.load_admin_event_summaries(db, [6807], include_recent=True))[6807]
    assert result.events == [recent]
    assert result.upstream_events == [upstream]
    assert "<= 200" in _sql(db.execute.call_args_list[1].args[0])
    recent_sql = _sql(db.execute.call_args_list[2].args[0])
    assert "ORDER BY task_events.id DESC" in recent_sql and "LIMIT 100" in recent_sql


@pytest.mark.asyncio
async def test_empty_summary_does_not_query():
    """空任务页无需读取事件表。"""
    db = SimpleNamespace(execute=AsyncMock())
    assert await admin_events.load_admin_event_summaries(db, []) == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_history_page_is_filtered_and_bounded():
    """历史在库端分页，事件前缀作为字面量匹配，不能放大为通配符。"""
    event = SimpleNamespace(id=5)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(scalar=6807), _Result(scalar=2), _Result([event])]))
    rows, total = await admin_events.list_admin_task_events(db, 6807, page=2, page_size=50, event_type_prefix="upstream.request.")
    assert rows == [event] and total == 2
    sql = _sql(db.execute.call_args_list[2].args[0])
    assert "task_events.task_id = 6807" in sql
    assert "upstream.request." in sql
    assert "ORDER BY task_events.id DESC" in sql
    assert "LIMIT 50 OFFSET 50" in sql


@pytest.mark.asyncio
async def test_event_prefix_does_not_treat_percent_as_wildcard():
    """用户提供的百分号按字面匹配，防止筛选扩大到全部历史。"""
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(scalar=6807), _Result(scalar=0), _Result()]))
    await admin_events.list_admin_task_events(db, 6807, page=1, page_size=50, event_type_prefix="upstream.%")
    assert "upstream./%%" in _sql(db.execute.call_args_list[2].args[0])


@pytest.mark.asyncio
async def test_missing_task_event_history_raises_without_loading_events():
    """不存在任务返回明确错误，不扫描孤立历史。"""
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result()))
    with pytest.raises(LookupError, match="任务不存在"):
        await admin_events.list_admin_task_events(db, 123, page=1, page_size=50)
    assert db.execute.await_count == 1


def test_admin_serializer_does_not_touch_or_replace_events_relationship():
    """有界响应不访问 ORM events，更不能改写会 delete-orphan 的历史关系。"""
    task = TaskRun(id=6807, domain="drama", task_type="fragment_video", status="failed", priority=100, requested_by=1,
                   cancel_requested=False, cancelable=True, progress_percent=0, billing_estimate_fen=0,
                   billing_charged_fen=0, billing_refunded_fen=0, billing_status="settled", steps=[], targets=[])
    assert "events" not in task.__dict__
    summary = admin_events.AdminEventSummary(events_total=64071, upstream_events_total=2)
    out = api._task_to_admin_out(task, "x@example.test", event_summary=summary)
    assert out.events == [] and out.events_total == 64071
    assert out.upstream_events == [] and out.upstream_events_total == 2
    assert "events" not in task.__dict__


@pytest.mark.asyncio
async def test_cancel_reads_no_history_and_writes_one_new_event(monkeypatch):
    """取消只读取任务和执行数据，不因历史膨胀阻塞取消。"""
    task = SimpleNamespace(id=6807, cancelable=True, status="running", current_step_key="submit")
    db = SimpleNamespace(add=AsyncMock(), commit=AsyncMock())
    db.add = lambda _event: None
    load = AsyncMock(return_value=task)
    monkeypatch.setattr(service, "get_task_admin", load)
    await service.cancel_task_admin(db, 6807)
    assert load.await_count == 2
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_events_http_contract_and_limits(monkeypatch):
    """分页接口有管理员鉴权，响应兼容约定并拒绝越界页长。"""
    event = SimpleNamespace(id=90000, event_type="upstream.request.failed", status="failed", phase="submit",
                            message="HTTP 422", payload={"status_code": 422}, created_at=None)
    query = AsyncMock(return_value=([event], 2))
    monkeypatch.setattr(api, "list_admin_task_events", query)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/admin")
    app.dependency_overrides[api.get_current_admin] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[api.get_db] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.get("/api/admin/tasks/6807/events", params={"page": 2, "page_size": 50, "event_type_prefix": "upstream.request."})
        assert response.status_code == 200
        assert response.json() == {"items": [{"id": 90000, "event_type": "upstream.request.failed", "status": "failed", "phase": "submit", "message": "HTTP 422", "payload": {"status_code": 422}, "created_at": None}], "total": 2, "page": 2, "page_size": 50}
        assert (await client.get("/api/admin/tasks/6807/events?page_size=201")).status_code == 422
        assert (await client.get("/api/admin/tasks/6807/events?page=0")).status_code == 422
        assert query.await_count == 1
        query.side_effect = LookupError("任务不存在")
        missing = await client.get("/api/admin/tasks/1/events")
        assert missing.status_code == 404 and missing.json() == {"detail": "任务不存在"}


@pytest.mark.asyncio
async def test_detail_route_supplies_separate_upstream_window(monkeypatch):
    """详情路由传递有界事件摘要，真实上游记录独立于最近生命周期日志。"""
    task = SimpleNamespace(id=6807, requested_by=1)
    summary = admin_events.AdminEventSummary(events_total=64071, upstream_events_total=2)
    monkeypatch.setattr(api, "get_task_admin", AsyncMock(return_value=task))
    monkeypatch.setattr(api, "_load_usage_lines", AsyncMock(return_value=[]))
    load_summary = AsyncMock(return_value={6807: summary})
    monkeypatch.setattr(api, "load_admin_event_summaries", load_summary)
    captured = {}

    def capture(_task, _email, _usage, *, event_summary):
        """记录传入序列化器的事件窗口。"""
        captured["summary"] = event_summary
        return "bounded"

    monkeypatch.setattr(api, "_task_to_admin_out", capture)
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(scalar="x@example.test")))
    assert await api.admin_get_task(6807, _admin=SimpleNamespace(), db=db) == "bounded"
    load_summary.assert_awaited_once_with(db, [6807], include_recent=True)
    assert captured["summary"] is summary
