# -*- coding: utf-8 -*-
"""科普/数字人工作台回归：删除、调序、重配音、取消标记、单段重做失败保留、上传画面、换音色。"""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import project_avatar, projects
from app.models import Project, ProjectStatus, Shot, ShotStatus, Template
from app.models_tasks import TaskRun
from app.schemas import ProjectUpdate, ShotRegenAudioIn, ShotReorderIn
from app.services import kepu_avatar, kepu_avatar_look, pipeline
from app.services.kepu_stages import VIDEO_SKIP_REASON_PRIVACY, resolve_kepu_billing_phase
from app.services.tasks import handlers

from tests.conftest import make_user


async def _project(
    db: AsyncSession, user, *, mode: str = "full", status: str = ProjectStatus.DONE, **fields
) -> tuple[Project, Shot, Shot]:
    """构造两镜项目：图、视频、配音齐备，默认已有成片。"""
    template = await db.get(Template, "tpl-studio-regress")
    if template is None:
        template = Template(id="tpl-studio-regress", name="回归模板", style_prefix="x", seedance_config={})
        db.add(template)
        await db.flush()
    project = Project(
        user_id=user.id, template_id=template.id, source_text="测试", pipeline_mode=mode,
        status=status, final_video_url="/static/final.mp4", **fields,
    )
    db.add(project)
    await db.flush()
    rows = []
    for no in (1, 2):
        shot = Shot(
            project_id=project.id, shot_no=no, narration=f"旁白{no}", img_prompt=f"场景{no}",
            image_url=f"/static/generated/p{project.id}/img_{no}.png",
            video_url=f"/static/generated/p{project.id}/v_{no}.mp4",
            audio_url=f"/static/generated/p{project.id}/a_{no}.mp3",
            status=ShotStatus.VIDEO_READY,
        )
        db.add(shot)
        rows.append(shot)
    await db.flush()
    return project, rows[0], rows[1]


def _bind_sessions(monkeypatch, db: AsyncSession) -> None:
    """把流水线与数字人模块自开的会话钉到用例事务，并屏蔽进度推送。"""
    @asynccontextmanager
    async def same_session():
        yield db

    for module in (pipeline, kepu_avatar, kepu_avatar_look):
        monkeypatch.setattr(module, "AsyncSessionLocal", same_session)
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(kepu_avatar, "publish_progress", AsyncMock())


# 第 1 条：删除项目
async def test_delete_project_with_shot_task_keeps_media_until_commit(db_session: AsyncSession, monkeypatch) -> None:
    """有单镜任务历史的项目可删除，且素材在数据库提交之后才清理。"""
    user = await make_user(db_session)
    project, shot, _ = await _project(db_session, user)
    task = TaskRun(domain="kepu", task_type="shot_regen_image", status="succeeded", requested_by=user.id,
                   project_id=project.id, shot_id=shot.id)
    db_session.add(task)
    await db_session.flush()
    order: list[str] = []
    commit = db_session.commit

    async def tracked_commit() -> None:
        await commit()
        order.append("commit")

    async def remove_assets(_project_id: int) -> None:
        order.append("assets")

    monkeypatch.setattr(db_session, "commit", tracked_commit)
    monkeypatch.setattr(projects.pipeline, "delete_project_assets", remove_assets)
    result = await projects.delete_project(project.id, db=db_session, user=user)
    assert result["ok"] is True
    assert order == ["commit", "assets"]
    await db_session.refresh(task)
    assert task.shot_id is None and task.project_id is None


# 第 2 条：调序作废整片旁白
async def test_reorder_invalidates_continuous_narration(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """图文模式调序后，旧顺序整片旁白作废，阶段回到 assets，与流水线恢复判定一致。"""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    narration = tmp_path / "full_narration.mp3"
    narration.write_bytes(b"x" * 3000)
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="image_text")
    out = await projects.reorder_shots(project.id, ShotReorderIn(shot_ids=[second.id, first.id]), db=db_session, user=user)
    assert not narration.exists()
    assert all(shot.audio_url is None for shot in out.shots)
    assert out.status == ProjectStatus.SCRIPT_READY
    assert resolve_kepu_billing_phase(out) == "assets"
    assert pipeline._continuous_audio_ok(project.id) is False


async def test_reorder_same_order_keeps_narration(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """顺序没变不重做配音，避免无谓重复扣费。"""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    narration = tmp_path / "full_narration.mp3"
    narration.write_bytes(b"x" * 3000)
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="image_text")
    out = await projects.reorder_shots(project.id, ShotReorderIn(shot_ids=[first.id, second.id]), db=db_session, user=user)
    assert narration.exists()
    assert all(shot.audio_url for shot in out.shots)


async def test_reorder_avatar_keeps_segment_audio(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """数字人每段配音与口型绑定，调序不作废。"""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="avatar")
    out = await projects.reorder_shots(project.id, ShotReorderIn(shot_ids=[second.id, first.id]), db=db_session, user=user)
    assert all(shot.audio_url and shot.video_url for shot in out.shots)


# 第 3 条：批量重配音一次提交
async def test_avatar_batch_audio_is_one_task(db_session: AsyncSession, monkeypatch) -> None:
    """数字人批量重配音只建一个任务，其余段随 payload 带上。"""
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="avatar")
    create = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(projects, "create_task", create)
    await projects.regen_audio(project.id, first.id, db_session, user, ShotRegenAudioIn(extra_shot_ids=[second.id]))
    assert create.await_count == 1
    assert create.await_args.args[2].payload["extra_shot_ids"] == [second.id]


async def test_batch_audio_rejects_foreign_shot(db_session: AsyncSession, monkeypatch) -> None:
    """附带的分镜必须属于本项目。"""
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="avatar")
    monkeypatch.setattr(projects, "create_task", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await projects.regen_audio(project.id, first.id, db_session, user, ShotRegenAudioIn(extra_shot_ids=[-1]))
    assert exc.value.status_code == 400


async def test_shot_audio_handler_forwards_extra_shots(monkeypatch) -> None:
    """任务执行时把批量段传给流水线。"""
    seen: dict = {}

    async def regen(project_id, shot_id, **kwargs):
        seen.update(project_id=project_id, shot_id=shot_id, **kwargs)

    monkeypatch.setattr(pipeline, "regen_shot_audio", regen)
    monkeypatch.setattr(handlers, "_kepu_output_snapshot", AsyncMock(return_value={}))
    task = SimpleNamespace(project_id=97201, shot_id=1, cancel_requested=False, payload={"extra_shot_ids": [2, 3]})
    await handlers._run_kepu_shot_audio(task)
    assert seen == {"project_id": 97201, "shot_id": 1, "extra_shot_ids": [2, 3]}


# 第 4 条：内置口播重配音同步拒绝
async def test_native_audio_regen_rejected_without_touching_project(db_session: AsyncSession, monkeypatch) -> None:
    """内置口播项目重配音返回 400，已完成状态不变，也不建任务。"""
    monkeypatch.setattr(pipeline, "_kepu_seedance_native_audio", lambda _project: True)
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="full")
    create = AsyncMock()
    monkeypatch.setattr(projects, "create_task", create)
    with pytest.raises(HTTPException) as exc:
        await projects.regen_audio(project.id, first.id, db=db_session, user=user)
    assert exc.value.status_code == 400
    create.assert_not_awaited()
    assert project.status == ProjectStatus.DONE
    assert project.native_video_audio is True


# 第 5 条：单镜任务清理残留取消标记
@pytest.mark.parametrize("handler_name,stage_name", [
    ("_run_kepu_shot_image", "regen_shot_image"),
    ("_run_kepu_shot_video", "regen_shot_video"),
    ("_run_kepu_shot_audio", "regen_shot_audio"),
    ("_run_kepu_project_audio", "regen_project_audio_and_compose"),
])
async def test_shot_task_clears_stale_cancel_marker(monkeypatch, handler_name, stage_name) -> None:
    """取消上一单镜任务留下的进程内标记，不影响新任务。"""
    task = SimpleNamespace(project_id=97202, shot_id=1, cancel_requested=False, payload={})
    pipeline.cancel_pipeline(task.project_id)

    async def stage(project_id, *_args, **_kwargs):
        assert not pipeline.is_cancelled(project_id)

    monkeypatch.setattr(pipeline, stage_name, stage)
    monkeypatch.setattr(handlers, "_kepu_output_snapshot", AsyncMock(return_value={}))
    try:
        assert (await getattr(handlers, handler_name)(task))["ok"]
    finally:
        pipeline.clear_pipeline_cancellation(task.project_id)


async def test_requested_cancel_skips_shot_stage(monkeypatch) -> None:
    """本任务已请求取消时不执行、不清标记。"""
    task = SimpleNamespace(project_id=97203, shot_id=1, cancel_requested=True, payload={})
    pipeline.cancel_pipeline(task.project_id)
    stage = AsyncMock()
    monkeypatch.setattr(pipeline, "regen_shot_image", stage)
    try:
        assert (await handlers._run_kepu_shot_image(task))["cancelled"]
        assert pipeline.is_cancelled(task.project_id)
    finally:
        pipeline.clear_pipeline_cancellation(task.project_id)


# 第 6 条：单段重做失败保留旧产物
async def test_segment_video_failure_keeps_old_video(db_session: AsyncSession, monkeypatch) -> None:
    """重生对口型视频失败时，旧视频仍在。"""
    _bind_sessions(monkeypatch, db_session)
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="avatar")
    old = first.video_url
    first.avatar_job_id = "stale"
    monkeypatch.setattr(kepu_avatar, "_one_segment", AsyncMock(side_effect=RuntimeError("GPU 失败")))
    with pytest.raises(RuntimeError):
        await kepu_avatar.regen_segment_video(project.id, first.id)
    assert first.video_url == old
    assert first.avatar_job_id is None


def _audio_env(monkeypatch, tmp_path: Path, tts) -> None:
    """配音测试环境：文件落临时目录，配音就绪与时长探测走桩。"""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    monkeypatch.setattr("app.services.kepu_stages.shot_audio_file_ok", lambda _shot: True)
    monkeypatch.setattr(kepu_avatar, "portrait_path", AsyncMock(return_value=tmp_path / "look.png"))
    monkeypatch.setattr("app.services.avatar_ai.is_configured", lambda: False)
    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(tts=tts))
    monkeypatch.setattr(kepu_avatar, "is_near_silent_audio", lambda _path: False)
    monkeypatch.setattr(kepu_avatar, "probe_duration", lambda _path: 3.0)


async def test_segment_audio_failure_restores_old_audio(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """重配音失败：配音地址、视频和被上游覆盖的旧音频文件都恢复。"""
    _bind_sessions(monkeypatch, db_session)
    dest = tmp_path / "shot_001_tts.mp3"
    dest.write_bytes(b"old-audio")

    async def broken_tts(*_args, **_kwargs):
        dest.write_bytes(b"broken")
        raise RuntimeError("配音失败")

    _audio_env(monkeypatch, tmp_path, broken_tts)
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="avatar")
    old_audio, old_video = first.audio_url, first.video_url
    with pytest.raises(RuntimeError):
        await kepu_avatar.regen_segment_audio(project.id, first.id)
    assert first.audio_url == old_audio and first.video_url == old_video
    assert dest.read_bytes() == b"old-audio"
    assert not list(tmp_path.glob("*.bak"))


async def test_segment_audio_batch_redoes_only_selected(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """批量重配音只重做所选段，成功后这些段的旧视频作废。"""
    _bind_sessions(monkeypatch, db_session)
    calls: list[int] = []

    async def tts(_text, _voice, *, shot_no, **_kwargs):
        calls.append(shot_no)
        path = tmp_path / f"shot_{shot_no:03d}_tts.mp3"
        path.write_bytes(b"new")
        return f"/static/generated/new_{shot_no}.mp3"

    _audio_env(monkeypatch, tmp_path, tts)
    monkeypatch.setattr("app.services.storage.local_path_from_url",
                        lambda url: tmp_path / f"shot_{url.rsplit('_', 1)[-1].split('.')[0].zfill(3)}_tts.mp3")
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="avatar")
    second_video = second.video_url
    await kepu_avatar.regen_segment_audio(project.id, first.id)
    assert calls == [1]
    assert first.video_url is None and second.video_url == second_video
    calls.clear()
    await kepu_avatar.regen_segment_audio(project.id, first.id, extra_shot_ids=[second.id])
    assert calls == [1, 2]


async def test_avatar_scene_failure_keeps_image_and_final(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """重画场景失败：旧场景图和成片都在。"""
    _bind_sessions(monkeypatch, db_session)
    monkeypatch.setattr(kepu_avatar, "portrait_path", AsyncMock(return_value=tmp_path / "look.png"))
    monkeypatch.setattr(project_avatar, "run_billed_image", AsyncMock(side_effect=RuntimeError("改图失败")))
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="avatar", avatar_image_url="/static/look.png")
    old_image = first.image_url
    with pytest.raises(HTTPException) as exc:
        await project_avatar.regen_avatar_scene(project.id, first.id, db=db_session, user=user)
    assert exc.value.status_code == 502
    assert first.image_url == old_image
    assert project.final_video_url == "/static/final.mp4"


async def test_avatar_scene_success_replaces_scene_and_invalidates_film(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """重画场景成功：即使旧图不是占位也会重画，该段视频与成片作废。"""
    _bind_sessions(monkeypatch, db_session)
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    monkeypatch.setattr("app.services.storage.publish_local", lambda _path: "/static/scene_new.png")
    monkeypatch.setattr(kepu_avatar, "portrait_path", AsyncMock(return_value=tmp_path / "look.png"))
    monkeypatch.setattr("app.services.avatar_ai.edit_image", AsyncMock())

    async def run(_db, _user, *, executor, **_kwargs):
        await executor()

    monkeypatch.setattr(project_avatar, "run_billed_image", run)
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="avatar", avatar_image_url="/static/look.png")
    second_image = second.image_url
    await project_avatar.regen_avatar_scene(project.id, first.id, db=db_session, user=user)
    assert first.image_url == "/static/scene_new.png" and first.video_url is None
    assert second.image_url == second_image
    assert project.final_video_url is None


# 第 7 条：数字人不走普通重绘
async def test_regen_image_rejected_for_avatar(db_session: AsyncSession, monkeypatch) -> None:
    """数字人普通重绘会丢掉人脸，接口直接拒绝。"""
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="avatar")
    create = AsyncMock()
    monkeypatch.setattr(projects, "create_task", create)
    with pytest.raises(HTTPException) as exc:
        await projects.regen_image(project.id, first.id, db=db_session, user=user)
    assert exc.value.status_code == 400
    create.assert_not_awaited()


# 第 8 条：同镜不重复提交
@pytest.mark.parametrize("endpoint", ["regen_image", "regen_video"])
async def test_shot_regen_rejects_active_task_on_same_shot(db_session: AsyncSession, monkeypatch, endpoint) -> None:
    """同镜已有活动任务时返回 409；其它镜可并行。"""
    user = await make_user(db_session)
    project, first, second = await _project(db_session, user, mode="full")
    db_session.add(TaskRun(domain="kepu", task_type="shot_regen_image", status="running", requested_by=user.id,
                           project_id=project.id, shot_id=first.id))
    await db_session.flush()
    create = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(projects, "create_task", create)
    with pytest.raises(HTTPException) as exc:
        await getattr(projects, endpoint)(project.id, first.id, db=db_session, user=user)
    assert exc.value.status_code == 409
    create.assert_not_awaited()
    await getattr(projects, endpoint)(project.id, second.id, db=db_session, user=user)
    create.assert_awaited_once()


# 第 9 条：上传画面清除隐私跳过
async def test_upload_image_clears_privacy_skip(db_session: AsyncSession, monkeypatch, tmp_path: Path) -> None:
    """换图后隐私结论失效，下一阶段重新出视频而不是直接合成。"""
    monkeypatch.setattr("app.services.storage.project_dir", lambda _pid: tmp_path)
    monkeypatch.setattr("app.services.storage.publish_local", lambda _path: "/static/upload.png")
    user = await make_user(db_session)
    project, first, _ = await _project(db_session, user, mode="full")
    first.video_url = None
    first.video_skip_reason = VIDEO_SKIP_REASON_PRIVACY
    first.avatar_job_id = "stale"
    upload = UploadFile(io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), filename="a.png",
                        headers={"content-type": "image/png"})
    out = await projects.upload_shot_image(project.id, first.id, upload, db=db_session, user=user)
    assert first.video_skip_reason is None and first.avatar_job_id is None
    assert resolve_kepu_billing_phase(out) == "videos"


# 第 10 条：数字人换音色
async def test_avatar_voice_change_invalidates_segments(db_session: AsyncSession) -> None:
    """数字人换音色后各段配音与对口型作废，阶段回到 assets。"""
    user = await make_user(db_session)
    project, *_ = await _project(db_session, user, mode="avatar", voice_id="voice_old")
    out = await projects.update_project(project.id, ProjectUpdate(voice_id="voice_new"), db=db_session, user=user)
    assert all(s.audio_url is None and s.video_url is None for s in out.shots)
    assert out.final_video_url is None
    assert out.status == ProjectStatus.SCRIPT_READY
    assert resolve_kepu_billing_phase(out) == "assets"


async def test_avatar_same_voice_keeps_segments(db_session: AsyncSession) -> None:
    """保存设置但音色没变，不作废已付费素材。"""
    user = await make_user(db_session)
    project, *_ = await _project(db_session, user, mode="avatar", voice_id="voice_old")
    out = await projects.update_project(project.id, ProjectUpdate(voice_id="voice_old"), db=db_session, user=user)
    assert all(s.audio_url and s.video_url for s in out.shots)
    assert out.final_video_url == "/static/final.mp4"


async def test_full_mode_voice_change_keeps_videos(db_session: AsyncSession) -> None:
    """非数字人模式换音色不动已有镜头。"""
    user = await make_user(db_session)
    project, *_ = await _project(db_session, user, mode="full", voice_id="voice_old")
    out = await projects.update_project(project.id, ProjectUpdate(voice_id="voice_new"), db=db_session, user=user)
    assert all(s.video_url for s in out.shots)
