"""Unit tests for project list runtime status projection."""

from types import SimpleNamespace

import pytest

from app.models import ProjectStatus
from app.services.kepu_projects import project_runtime_view as _project_runtime_view


def _proj(**kwargs):
    return SimpleNamespace(
        status=kwargs.get("status", ProjectStatus.VIDEOING),
        progress=kwargs.get("progress", 81),
        error_msg=kwargs.get("error_msg"),
        active_tasks=kwargs.get("active_tasks", []),
    )


def _task(*, task_type="project_pipeline", status="running", phase="videos", progress=0):
    return SimpleNamespace(
        task_type=task_type,
        status=status,
        progress_percent=progress,
        error_message=None,
        payload={"phase": phase, "project_id": 404},
    )


def test_pipeline_videos_phase_shows_videoing_not_scripting():
    p = _proj(active_tasks=[_task(phase="videos")])
    status, progress, _err = _project_runtime_view(p)
    assert status == ProjectStatus.VIDEOING
    assert progress == 81


def test_pipeline_assets_phase_shows_imaging():
    p = _proj(status=ProjectStatus.IMAGING, progress=40, active_tasks=[_task(phase="assets")])
    status, _, _ = _project_runtime_view(p)
    assert status == ProjectStatus.IMAGING


def test_pipeline_script_phase_shows_scripting():
    p = _proj(status=ProjectStatus.SCRIPTING, progress=5, active_tasks=[_task(phase="script")])
    status, _, _ = _project_runtime_view(p)
    assert status == ProjectStatus.SCRIPTING


def test_no_active_task_keeps_project_status():
    p = _proj(status=ProjectStatus.VIDEOING, progress=81, active_tasks=[])
    status, progress, _ = _project_runtime_view(p)
    assert status == ProjectStatus.VIDEOING
    assert progress == 81


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        ("shot_regen_image", ProjectStatus.IMAGING),
        ("shot_regen_video", ProjectStatus.VIDEOING),
        ("shot_regen_audio", ProjectStatus.AUDIOING),
        ("project_regen_audio", ProjectStatus.AUDIOING),
        ("project_compose_only", ProjectStatus.COMPOSING),
        ("unknown", ProjectStatus.DONE),
    ],
)
def test_side_tasks_keep_status_projection(task_type, expected):
    """各任务类型沿用原列表展示状态，未知类型保留项目状态。"""
    project = _proj(status=ProjectStatus.DONE, active_tasks=[_task(task_type=task_type, progress=90)])
    assert _project_runtime_view(project) == (expected, 90, None)


@pytest.mark.parametrize("task_status", ["failed", "cancelled"])
def test_terminal_task_keeps_its_error_and_progress(task_status):
    """终态任务按任务自身进度展示，错误为空时回退项目错误。"""
    task = _task(status=task_status, progress=20)
    project = _proj(progress=81, error_msg="原项目错误", active_tasks=[task])
    assert _project_runtime_view(project) == (task_status.upper(), 20, "原项目错误")
    task.error_message = "任务错误"
    assert _project_runtime_view(project)[2] == "任务错误"


def test_pipeline_unknown_phase_keeps_running_stage_or_defaults_to_scripting():
    """旧任务缺少 phase 时优先沿用进行中阶段，否则显示拆分镜。"""
    project = _proj(status=ProjectStatus.AUDIOING, active_tasks=[_task(phase="")])
    assert _project_runtime_view(project)[0] == ProjectStatus.AUDIOING
    project.status = ProjectStatus.DRAFT
    assert _project_runtime_view(project)[0] == ProjectStatus.SCRIPTING


def test_completed_task_does_not_override_persisted_project_view():
    """终态成功记录混入活动列表时，仍使用项目持久化进度和错误。"""
    project = _proj(error_msg="项目错误", active_tasks=[_task(status="succeeded", progress=100)])
    assert _project_runtime_view(project) == (ProjectStatus.VIDEOING, 81, "项目错误")
