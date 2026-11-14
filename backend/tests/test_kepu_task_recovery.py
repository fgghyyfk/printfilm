"""科普重试原子性、任务门禁及执行失败后的项目恢复回归。"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import projects
from app.models import Project, ProjectStatus, Shot, Template
from app.models_tasks import TaskRun
from app.schemas import ProjectUpdate
from app.services.tasks import executor
from app.services.public_errors import InsufficientBalanceError
from tests.conftest import make_task, make_user


async def seed_project(db, *, status=ProjectStatus.DONE):
    """创建含旧成片和一镜素材的归属项目。"""
    user = await make_user(db)
    tpl = Template(id=f"recovery-{uuid4().hex}", name="恢复模板", style_prefix="x")
    db.add(tpl)
    await db.flush()
    project = Project(user_id=user.id, template_id=tpl.id, source_text="测试", status=status,
                      final_video_url="/static/old.mp4", error_msg="旧错误")
    db.add(project)
    await db.flush()
    shot = Shot(project_id=project.id, shot_no=1, image_url="/static/old.png", narration="测试")
    db.add(shot)
    await db.commit()
    return user, project, shot


# 五个入口均需要先完成任务建档，再一次性提交状态。
REGEN_ROUTES = [projects.regen_image, projects.regen_video, projects.regen_audio,
                projects.regen_all_audio, projects.compose_only]


async def call_regen(route, db, user, project_id, shot_id):
    """兼容单镜和整片入口的真实函数调用。"""
    kwargs = {"db": db, "user": user, "project_id": project_id}
    if route in REGEN_ROUTES[:3]:
        kwargs["shot_id"] = shot_id
    return await route(**kwargs)


@pytest.mark.parametrize("route", REGEN_ROUTES)
async def test_create_failure_does_not_commit_running_state_or_clear_final(db_session, route):
    """建任务失败后，数据库仍保留原状态和旧成片。"""
    user, project, shot = await seed_project(db_session)
    project_id, shot_id = project.id, shot.id
    with patch.object(projects, "create_task", AsyncMock(side_effect=ValueError("余额不足"))):
        with pytest.raises(HTTPException):
            await call_regen(route, db_session, user, project_id, shot_id)
    row = (await db_session.execute(select(Project.status, Project.final_video_url, Project.error_msg)
                                    .where(Project.id == project_id))).one()
    assert row == (ProjectStatus.DONE, "/static/old.mp4", "旧错误")
    assert not (await db_session.execute(select(TaskRun).where(TaskRun.project_id == project_id))).scalars().all()


@pytest.mark.parametrize("route", REGEN_ROUTES)
async def test_regen_commits_task_and_project_together(db_session, monkeypatch, route):
    """真实create_task写入完成前，入口不得裸提交项目状态。"""
    if route is projects.regen_audio:
        # 视频内置口播时单镜重配音在建任务前即被拒绝；此处验证外部配音模式的提交边界。
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", False)
    user, project, shot = await seed_project(db_session)
    commit = db_session.commit
    snapshots = []

    async def record_commit():
        """在每次提交边界读取真实数据库中的任务。"""
        tasks = (await db_session.execute(select(TaskRun).where(TaskRun.project_id == project.id))).scalars().all()
        snapshots.append([task.task_type for task in tasks])
        await commit()

    with (patch.object(db_session, "commit", record_commit),
          patch("app.services.billing.settlement.ensure_balance_for_task", AsyncMock(return_value=0))):
        result = await call_regen(route, db_session, user, project.id, shot.id)
    assert len(snapshots) == 1
    assert len(snapshots[0]) == 1
    assert result.status in {ProjectStatus.IMAGING, ProjectStatus.VIDEOING, ProjectStatus.AUDIOING, ProjectStatus.COMPOSING}
    assert result.active_tasks


@pytest.mark.parametrize("action", ["patch", "generate", "regen_all", "compose"])
async def test_stale_running_status_without_task_allows_action(db_session, action):
    """残留IMAGING不再阻止编辑或重新生成。"""
    user, project, shot = await seed_project(db_session, status=ProjectStatus.IMAGING)
    with patch("app.services.billing.settlement.ensure_balance_for_task", AsyncMock(return_value=0)):
        if action == "patch":
            result = await projects.update_project(project.id, ProjectUpdate(title="可编辑"), db_session, user)
            assert result.title == "可编辑"
        elif action == "generate":
            result = await projects.generate_project(project.id, False, db_session, user)
            assert result.active_tasks
        else:
            await call_regen(projects.regen_all_audio if action == "regen_all" else projects.compose_only,
                             db_session, user, project.id, shot.id)


@pytest.mark.parametrize("task_status", ["running", "cancel_requested"])
@pytest.mark.parametrize("task_type", ["project_compose_only", "content_expand"])
@pytest.mark.parametrize("action", ["patch", "generate", "regen_all", "compose"])
async def test_active_task_blocks_project_action(db_session, task_status, task_type, action):
    """取消请求未终止前，含合成任务的项目仍禁止编辑及整片再生成。"""
    user, project, shot = await seed_project(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type=task_type, status=task_status)
    task.project_id = project.id
    task.cancel_requested = task_status == "cancel_requested"
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        if action == "patch":
            await projects.update_project(project.id, ProjectUpdate(title="不应修改"), db_session, user)
        elif action == "generate":
            await projects.generate_project(project.id, False, db_session, user)
        else:
            await call_regen(projects.regen_all_audio if action == "regen_all" else projects.compose_only,
                             db_session, user, project.id, shot.id)
    assert exc.value.status_code == 409


async def test_busy_guard_does_not_include_other_owners_tasks(db_session):
    """任务过滤不能跨owner，项目鉴权也不能跨owner。"""
    user, project, _ = await seed_project(db_session)
    other = await make_user(db_session)
    task = await make_task(db_session, other, domain="kepu", task_type="project_pipeline", status="running")
    task.project_id = project.id
    await db_session.commit()
    result = await projects.update_project(project.id, ProjectUpdate(title="本人可改"), db_session, user)
    assert result.title == "本人可改"
    with pytest.raises(HTTPException) as exc:
        await projects.update_project(project.id, ProjectUpdate(title="越权"), db_session, other)
    assert exc.value.status_code == 404


async def test_cancel_uses_active_task_not_persisted_status(db_session):
    """残留DONE但存在运行任务仍可取消，并调用现有流水线取消。"""
    user, project, _ = await seed_project(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type="content_expand", status="running")
    task.project_id = project.id
    await db_session.commit()
    with patch.object(projects.pipeline, "cancel_pipeline") as cancel:
        result = await projects.cancel_project(project.id, db_session, user)
    cancel.assert_called_once_with(project.id)
    assert result.active_tasks[0].status == "cancel_requested"
    assert result.active_tasks[0].cancel_requested


@pytest.mark.parametrize("failure,public_reason,error_code", [
    (InsufficientBalanceError("余额不足"), "余额不足", "insufficient_balance"),
    (RuntimeError("连接中断"), "预扣失败", "RuntimeError"),
])
async def test_freeze_failure_recovers_kepu_project(db_session, failure, public_reason, error_code):
    """真实执行器预扣失败必须同步恢复项目可编辑，并保留中文原因。"""
    user, project, shot = await seed_project(db_session, status=ProjectStatus.IMAGING)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="leased")
    task.project_id, task.shot_id = project.id, shot.id
    await db_session.commit()

    @asynccontextmanager
    async def same_session():
        """执行器共享隔离测试事务。"""
        yield db_session

    with (patch.object(executor, "AsyncSessionLocal", same_session),
          patch.object(executor, "freeze_for_task", AsyncMock(side_effect=failure))):
        await executor.execute_task_run(task.id)
    row = (await db_session.execute(select(Project.status, Project.error_msg).where(Project.id == project.id))).one()
    assert row.status == ProjectStatus.FAILED
    assert public_reason in row.error_msg
    assert "连接中断" not in row.error_msg
    assert task.status == "failed"
    assert task.error_code == error_code
    result = await projects.update_project(project.id, ProjectUpdate(title="恢复编辑"), db_session, user)
    assert result.title == "恢复编辑"


async def test_regen_handler_exception_already_recovers_project(db_session):
    """真实handler→regen抛异常→执行器现有_fail_task恢复，不添加pipeline重复处理。"""
    user, project, shot = await seed_project(db_session, status=ProjectStatus.IMAGING)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="leased")
    task.project_id, task.shot_id = project.id, shot.id
    await db_session.commit()

    @asynccontextmanager
    async def same_session():
        """执行器共享隔离测试事务。"""
        yield db_session

    with (patch.object(executor, "AsyncSessionLocal", same_session),
          patch.object(executor, "freeze_for_task", AsyncMock()),
          patch("app.services.pipeline.regen_shot_image", AsyncMock(side_effect=RuntimeError("图片生成失败")))):
        await executor.execute_task_run(task.id)
    assert task.status == "failed"
    row = (await db_session.execute(select(Project.status, Project.error_msg).where(Project.id == project.id))).one()
    assert row.status == ProjectStatus.FAILED
    assert "任务执行失败" in row.error_msg
    assert "图片生成失败" not in row.error_msg
    assert task.error_code == "RuntimeError"
    result = await projects.update_project(project.id, ProjectUpdate(title="失败后仍可编辑"), db_session, user)
    assert result.title == "失败后仍可编辑"
    with patch("app.services.billing.settlement.ensure_balance_for_task", AsyncMock(return_value=0)):
        result = await projects.generate_project(project.id, False, db_session, user)
    assert result.active_tasks[0].task_type == "project_pipeline"


async def test_cancel_rejects_stale_status_without_tasks(db_session):
    """无活动任务的残留运行状态不发出虚假的取消请求。"""
    user, project, _ = await seed_project(db_session, status=ProjectStatus.IMAGING)
    with patch.object(projects.pipeline, "cancel_pipeline") as cancel:
        with pytest.raises(HTTPException) as exc:
            await projects.cancel_project(project.id, db_session, user)
    assert exc.value.status_code == 400
    cancel.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["handler", "freeze"])
async def test_parallel_regen_success_then_failure_preserves_stable_status_and_reason(db_session, failure_stage):
    """两镜同时活动：A真实重绘成功写IMAGE_READY后，B失败仍须让项目展示中文原因。"""
    from types import SimpleNamespace
    from app.services import pipeline

    user, project, shot = await seed_project(db_session, status=ProjectStatus.IMAGING)
    project.error_msg = None
    second_shot = Shot(project_id=project.id, shot_no=2, img_prompt="另一镜", narration="测试")
    db_session.add(second_shot)
    await db_session.flush()
    task_a = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="running")
    task_b = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="running")
    task_a.project_id, task_a.shot_id = project.id, shot.id
    task_b.project_id, task_b.shot_id = project.id, second_shot.id
    await db_session.commit()

    @asynccontextmanager
    async def same_session():
        """执行器和实际流水线共用隔离测试事务。"""
        yield db_session

    # 仅替换上游AI和记账；运行真实handler与真实regen写回。
    ark = SimpleNamespace(gen_image=AsyncMock(side_effect=[
        SimpleNamespace(local_url="/static/new.png", remote_url="https://upstream.test/new.png"),
        RuntimeError("第二镜图片生成失败"),
    ]))
    # handler 收尾的产物快照经 app.database 取会话，同样指向隔离事务。
    with (patch.object(executor, "AsyncSessionLocal", same_session),
          patch.object(pipeline, "AsyncSessionLocal", same_session),
          patch("app.database.AsyncSessionLocal", same_session),
          patch.object(pipeline, "get_ark", return_value=ark),
          patch.object(pipeline, "_record_seedream_usage", AsyncMock()),
          patch.object(executor, "freeze_for_task", AsyncMock()) as freeze):
        await executor.execute_task_run(task_a.id)
        assert task_a.status == "succeeded"
        assert task_b.status == "running"
        assert project.status == ProjectStatus.IMAGE_READY
        if failure_stage == "freeze":
            freeze.side_effect = InsufficientBalanceError("余额不足")
        await executor.execute_task_run(task_b.id)

    row = (await db_session.execute(select(Project.status, Project.error_msg).where(Project.id == project.id))).one()
    assert row.status == ProjectStatus.IMAGE_READY
    assert task_b.status == "failed"
    assert row.error_msg and "生成失败" in row.error_msg
    assert "第二镜图片生成失败" not in row.error_msg
    assert ("余额不足" if failure_stage == "freeze" else "任务执行失败") in row.error_msg
    assert task_b.shot_id == second_shot.id
    assert task_b.error_code == ("insufficient_balance" if failure_stage == "freeze" else "RuntimeError")
    assert shot.image_url == "/static/new.png"
    result = await projects.update_project(project.id, ProjectUpdate(title="部分失败后可编辑"), db_session, user)
    assert result.title == "部分失败后可编辑"


@pytest.mark.parametrize("status", [ProjectStatus.IMAGE_READY, ProjectStatus.VIDEO_READY,
                                    ProjectStatus.DONE, ProjectStatus.CANCELLED])
async def test_freeze_failure_reports_stable_project_without_overwriting_completion_or_cancellation(db_session, status):
    """稳定态预扣失败保留可编辑状态；新完成或取消的项目状态与文案均受保护。"""
    user, project, shot = await seed_project(db_session, status=status)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="leased")
    task.project_id, task.shot_id = project.id, shot.id
    await db_session.commit()

    @asynccontextmanager
    async def same_session():
        """执行器共享隔离测试事务。"""
        yield db_session

    with (patch.object(executor, "AsyncSessionLocal", same_session),
          patch.object(executor, "freeze_for_task", AsyncMock(side_effect=InsufficientBalanceError("余额不足")))):
        await executor.execute_task_run(task.id)
    row = (await db_session.execute(select(Project.status, Project.error_msg).where(Project.id == project.id))).one()
    assert row.status == status
    if status in {ProjectStatus.DONE, ProjectStatus.CANCELLED}:
        assert row.error_msg == "旧错误"
    else:
        assert row.error_msg and "生成失败" in row.error_msg and "余额不足" in row.error_msg
