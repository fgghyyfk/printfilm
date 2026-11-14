"""重做要等新稿成功才替换旧稿；状态与任务同事务，失败不能把项目卡在进行中。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import projects
from app.models import Project, ProjectStatus, Shot
from app.schemas import ProjectUpdate, ShotUpdate
from app.services import pipeline, storage
from app.services.ark import StoryboardResult
from app.services.billing import settlement
from app.services.tasks import executor
from tests.conftest import make_task
from tests.drama_fragment_helpers import seed_project


def _no_balance():
    """模拟入队时余额不足。"""
    return patch.object(settlement, "ensure_balance_for_task", AsyncMock(side_effect=ValueError("余额不足：需要 100 分")))


def _enough_balance():
    """模拟入队时余额足够。"""
    return patch.object(settlement, "ensure_balance_for_task", AsyncMock(return_value=0))


@pytest.fixture
def media(tmp_path, monkeypatch):
    """把项目目录放到临时路径，并放一组旧文件。"""
    monkeypatch.setattr(storage, "project_dir", lambda _pid: tmp_path)
    (tmp_path / "old.png").write_bytes(b"old image")
    (tmp_path / "old.mp4").write_bytes(b"old video")
    (tmp_path / "full_narration.mp3").write_bytes(b"old audio")
    (tmp_path / "cover.png").write_bytes(b"uploaded cover")
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: tmp_path / url.rsplit("/", 1)[-1] if url else None)
    return tmp_path


async def _with_old_media(db, *, status=ProjectStatus.DONE):
    """已完成项目：有旧分镜和文件地址。"""
    user, project, shot = await seed_project(db, status=status)
    shot.image_url = "/static/p/old.png"
    shot.video_url = "/static/p/old.mp4"
    await db.commit()
    return user, project, shot


def _pipeline_session(monkeypatch, db, plans):
    """让剧本阶段走测试会话，模型返回给定分镜。"""
    @asynccontextmanager
    async def session():
        yield db
    planner = plans if isinstance(plans, AsyncMock) else AsyncMock(return_value=plans)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(chat_storyboard=planner))
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(pipeline, "_record_usage_est", AsyncMock())


async def test_restart_insufficient_balance_keeps_everything(db_session, media):
    """余额不足点重做：稿子和文件都不能动。"""
    user, project, shot = await _with_old_media(db_session)
    project_id, shot_id = project.id, shot.id
    with _no_balance(), pytest.raises(HTTPException) as exc:
        await projects.generate_project(project_id, True, db_session, user)
    assert exc.value.status_code == 402
    assert await db_session.get(Shot, shot_id) is not None
    assert (media / "old.png").exists() and (media / "old.mp4").exists()


async def test_restart_keeps_old_work_until_new_storyboard(db_session, media):
    """入队成功也先不删：新稿可能失败，旧稿要还在。"""
    user, project, shot = await _with_old_media(db_session)
    old_final = project.final_video_url
    with _enough_balance():
        result = await projects.generate_project(project.id, True, db_session, user)
    assert [s.id for s in result.shots] == [shot.id]
    assert result.final_video_url == old_final
    assert (media / "old.mp4").exists()
    assert result.active_tasks[0].payload["restart"] is True


async def test_failed_new_storyboard_keeps_old_work(db_session, media, monkeypatch):
    """模型写稿失败：旧分镜和文件都还在。"""
    _, project, shot = await _with_old_media(db_session)
    _pipeline_session(monkeypatch, db_session, AsyncMock(side_effect=RuntimeError("模型超时")))
    with pytest.raises(RuntimeError):
        await pipeline._script_stage(project.id)
    assert await db_session.get(Shot, shot.id) is not None
    assert (media / "old.mp4").exists() and (media / "full_narration.mp3").exists()


async def test_new_storyboard_replaces_old_and_detaches_history(db_session, media, monkeypatch):
    """新稿成功才替换：单镜任务历史不挡路，旧文件清掉，用户上传的文件保留。"""
    user, project, shot = await _with_old_media(db_session)
    history = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="succeeded")
    history.shot_id = shot.id
    await db_session.commit()
    plan = StoryboardResult(shots=[], character_bible="无人物")
    _pipeline_session(monkeypatch, db_session, plan)
    await pipeline._script_stage(project.id)
    assert (await db_session.scalars(select(Shot).where(Shot.project_id == project.id))).all() == []
    await db_session.refresh(history)
    assert history.shot_id is None
    assert not (media / "old.png").exists() and not (media / "old.mp4").exists()
    assert not (media / "full_narration.mp3").exists(), "旧整片音轨留着会被新稿当成现成配音"
    assert (media / "cover.png").exists()




@pytest.mark.parametrize("task_type", ["project_pipeline", "project_compose_only"])
async def test_edit_rejected_while_project_task_active(db_session, media, task_type):
    """合成中改稿会让成片和稿子对不上，还会删掉正在用的音轨。"""
    user, project, shot = await _with_old_media(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type=task_type, status="running")
    task.project_id = project.id
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await projects.update_shot(project.id, shot.id, ShotUpdate(narration="新"), db_session, user)
    assert exc.value.status_code == 409
    assert (media / "full_narration.mp3").exists()


@pytest.mark.parametrize("call", [
    lambda p, s, db, u: projects.regen_image(p, s, db, u),
    lambda p, s, db, u: projects.regen_video(p, s, db, u),
    lambda p, s, db, u: projects.regen_audio(p, s, db, u),
    lambda p, s, db, u: projects.regen_all_audio(p, db, u),
    lambda p, s, db, u: projects.compose_only(p, db, u),
], ids=["image", "video", "audio", "all_audio", "compose"])
async def test_regen_insufficient_balance_does_not_stick_status(db_session, media, call, monkeypatch):
    """建任务失败时状态不变，不能卡在进行中。"""
    # 走外部配音：内置口播的重配音在建任务前就会被 400 拒绝，到不了余额校验。
    monkeypatch.setattr("app.services.pipeline._kepu_seedance_native_audio", lambda _project: False)
    user, project, shot = await _with_old_media(db_session, status=ProjectStatus.VIDEO_READY)
    project_id, shot_id = project.id, shot.id
    with _no_balance(), pytest.raises(HTTPException) as exc:
        await call(project_id, shot_id, db_session, user)
    assert exc.value.status_code == 402
    fresh = (await db_session.execute(select(Project.status).where(Project.id == project_id))).scalar_one()
    assert fresh == ProjectStatus.VIDEO_READY


async def test_freeze_failure_restores_project(db_session):
    """排队时余额够、真正预扣时不够：项目不能停在进行中。"""
    user, project, _ = await seed_project(db_session, status=ProjectStatus.IMAGING)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="running")
    task.project_id = project.id
    await db_session.commit()
    await executor._fail_task_before_start(db_session, task, None, error_code="insufficient_balance", message="余额不足")
    await db_session.refresh(project)
    assert project.status == ProjectStatus.FAILED and "余额不足" in project.error_msg


async def test_stale_running_status_without_tasks_can_retry(db_session, media):
    """历史上已卡住的项目：没有活跃任务就能修改和重试。"""
    user, project, shot = await _with_old_media(db_session, status=ProjectStatus.IMAGING)
    shot.video_url = None
    await db_session.commit()
    updated = await projects.update_project(project.id, ProjectUpdate(title="改名"), db_session, user)
    assert updated.title == "改名"
    with _enough_balance():
        result = await projects.generate_project(project.id, False, db_session, user)
    assert result.active_tasks


async def test_active_task_still_blocks_whole_project_actions(db_session, media):
    """有活跃任务时整片操作仍然拒绝。"""
    user, project, _ = await _with_old_media(db_session, status=ProjectStatus.VIDEO_READY)
    task = await make_task(db_session, user, domain="kepu", task_type="shot_regen_image", status="running")
    task.project_id = project.id
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await projects.generate_project(project.id, True, db_session, user)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["pending", "cancel_requested"])
async def test_edit_rejects_queued_or_cancelling_compose(db_session, media, status):
    """合成任务排队或取消中同样占用稿件与音轨，改稿须 409 且不动文件。"""
    user, project, shot = await _with_old_media(db_session)
    task = await make_task(db_session, user, domain="kepu", task_type="project_compose_only", status=status)
    task.project_id = project.id
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await projects.update_shot(project.id, shot.id, ShotUpdate(narration="新"), db_session, user)
    assert exc.value.status_code == 409
    assert (media / "full_narration.mp3").read_bytes() == b"old audio"
