"""项目列表活动任务必须批量读取，并保持用户隔离和新任务优先。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.services.tasks.service import (
    ACTIVE_TASK_STATUSES,
    list_active_tasks_for_owner,
    list_active_tasks_for_projects,
)


# 模拟 SQLAlchemy 结果链；保留任务顺序供批量分组断言。
def _result(tasks):
    rows = SimpleNamespace(all=lambda: tasks)
    rows.unique = lambda: rows
    return SimpleNamespace(scalars=lambda: rows)


async def test_empty_projects_skip_database():
    """空列表无需发出 IN 查询。"""
    db = SimpleNamespace(execute=AsyncMock())
    assert await list_active_tasks_for_projects(db, 7, []) == {}
    db.execute.assert_not_awaited()


async def test_projects_use_one_scoped_query_and_keep_task_order():
    """一页项目只查一次任务，且无任务的项目仍返回空列表。"""
    newest = SimpleNamespace(project_id=12, id=3)
    older = SimpleNamespace(project_id=12, id=1)
    other = SimpleNamespace(project_id=20, id=2)
    db = SimpleNamespace(execute=AsyncMock(return_value=_result([newest, other, older])))

    grouped = await list_active_tasks_for_projects(db, 7, [12, 20, 30, 12])

    assert grouped == {12: [newest, older], 20: [other], 30: []}
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(statement)
    assert "task_runs.requested_by =" in sql
    assert "task_runs.project_id IN" in sql
    assert "task_runs.status IN" in sql
    assert "ORDER BY task_runs.created_at DESC" in sql
    assert 7 in statement.params.values()
    assert [12, 20, 30] in statement.params.values()
    assert any(set(value) == ACTIVE_TASK_STATUSES for value in statement.params.values() if isinstance(value, (list, tuple)))


async def test_projects_without_tasks_return_empty_groups():
    """整页都没有任务时，仍保留所有项目供列表直接索引。"""
    db = SimpleNamespace(execute=AsyncMock(return_value=_result([])))
    assert await list_active_tasks_for_projects(db, 7, [12, 20]) == {12: [], 20: []}
    db.execute.assert_awaited_once()


async def test_owner_without_scope_skips_database():
    """原单项目查询未传范围时不扩大到用户全部任务。"""
    db = SimpleNamespace(execute=AsyncMock())
    assert await list_active_tasks_for_owner(db, 7) == []
    db.execute.assert_not_awaited()


async def test_owner_keeps_project_and_drama_union_scope():
    """共享查询构造后仍兼容两个项目范围的或条件。"""
    db = SimpleNamespace(execute=AsyncMock(return_value=_result([])))
    assert await list_active_tasks_for_owner(db, 7, project_id=12, drama_project_id=20) == []
    statement = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "(task_runs.project_id =" in str(statement)
    assert " OR task_runs.drama_project_id =" in str(statement)
