"""科普列表编排回归：筛选、分页、任务运行态与统计共用明确的数据范围。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.models import ProjectStatus
from app.services import kepu_projects


# 包装固定的计数或 ORM 行，供离线服务测试模拟数据库返回。
def _result(value):
    return SimpleNamespace(scalar_one=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: value))


# 构造带完整列表字段的项目，避免测试依赖业务数据库。
def _project(project_id):
    return SimpleNamespace(
        id=project_id, title=f"项目 {project_id}", template_id="test", status=ProjectStatus.DONE,
        progress=30, cover_url=None, final_video_url=None, error_msg="旧错误", pipeline_mode="full",
        output_ratio="16:9", created_at=datetime(2026, 10, 20, tzinfo=UTC), updated_at=None,
    )


async def test_listing_batches_tasks_and_projects_runtime_once(monkeypatch):
    """列表一次获取整页任务，运行态与已发布标记保持原响应语义。"""
    rows = [_project(12), _project(20)]
    task = SimpleNamespace(
        id=99, domain="kepu", task_type="project_pipeline", status="running",
        progress_percent=60, error_message=None, payload={"phase": "videos"},
    )
    batch = AsyncMock(return_value={12: [task], 20: []})
    monkeypatch.setattr(kepu_projects, "list_active_tasks_for_projects", batch)
    # 列表总数、当前页、发布 ID、全部/生成中/完成/已发布统计。
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_result(value) for value in [9, rows, [20], 10, 2, 5, 1]]))

    page = await kepu_projects.list_project_page(
        db, 7, page=2, page_size=2, status="done", q="测试", pipeline_mode="full",
    )

    batch.assert_awaited_once_with(db, 7, [12, 20])
    assert page.meta.model_dump() == {"page": 2, "page_size": 2, "total": 9}
    assert page.stats.model_dump() == {"total": 10, "generating": 2, "done": 5, "published": 1}
    assert (page.items[0].status, page.items[0].progress, page.items[0].error_msg) == (ProjectStatus.VIDEOING, 60, None)
    assert [item.published for item in page.items] == [False, True]
    assert page.items[1].error_msg == "旧错误"

    compiled = [call.args[0].compile(dialect=postgresql.dialect()) for call in db.execute.await_args_list]
    # 统计与当前页都保持用户/关键词/类型范围；Tab 仅约束当前页与其总数。
    for index in [0, 1, 3, 4, 5, 6]:
        assert "projects.user_id =" in str(compiled[index])
        assert "%测试%" in compiled[index].params.values()
        assert "full" in compiled[index].params.values()
    assert "projects.status =" in str(compiled[0])
    assert "projects.status =" not in str(compiled[3])
    assert 2 in compiled[1].params.values()  # 分页大小与第二页偏移均为 2。


async def test_empty_page_skips_task_and_publish_queries(monkeypatch):
    """超出末页时仍返回统计，不为不存在的项目查询发布或活动任务。"""
    batch = AsyncMock()
    monkeypatch.setattr(kepu_projects, "list_active_tasks_for_projects", batch)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_result(value) for value in [0, [], 0, 0, 0, 0]]))

    page = await kepu_projects.list_project_page(db, 7, page=1, page_size=8)

    assert page.items == []
    assert page.stats.total == 0
    batch.assert_not_awaited()
    assert db.execute.await_count == 6
