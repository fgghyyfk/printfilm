"""分镜参数与成片元数据快照：离线执行，不发生成请求。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models import User
from app.models_drama import DramaEpisode, DramaEpisodeFragment
from app.models_tasks import TaskRun
from app.services.drama.generation import FragmentVideoPrepared, write_fragment_video_output_meta
from app.services.tasks.generation_metadata import merge_generation_params, submitted_generation_params


def test_public_submission_parameters_preserve_false_zero_and_image_count_alias():
    """已知公开开关、音色及图片张数留痕，不能因 false/0 被当成缺失而丢掉。"""
    submitted = submitted_generation_params({
        "model": "image-model", "n": 0, "seed": 0, "speaker": "voice-a",
        "metadata": {"watermark": False, "camera_fixed": False, "generate_audio": False, "api_key": "private"},
    })
    params = merge_generation_params({"count": 3, "watermark": True, "camera_fixed": True}, submitted)
    assert params["count"] == params["n"] == 0 and params["seed"] == 0
    assert params["watermark"] is False and params["camera_fixed"] is False
    assert params["generate_audio"] is False and params["speaker"] == "voice-a"
    assert "private" not in str(params)


async def test_fragment_submission_keeps_parameters_after_discarding_prepared_context(monkeypatch):
    """NIO prepare→submit 丢弃私有素材上下文后，真实提示词和规格仍持久化。"""
    from app.services.drama import jobs
    from app.services.tasks import service

    task = TaskRun(id=5, requested_by=1, fragment_id=9, episode_id=3, payload={}, steps=[], progress_percent=0)
    project = SimpleNamespace(id=2)
    episode = SimpleNamespace(id=3, project=project)
    fragment = SimpleNamespace(id=9, episode_id=3, params={})

    async def get_entity(model, _id, **_kwargs):
        """只提供执行器所需的已授权目标，不访问数据库。"""
        return {DramaEpisode: episode, DramaEpisodeFragment: fragment, User: SimpleNamespace(id=1)}[model]

    db = SimpleNamespace(get=get_entity, commit=AsyncMock())

    @asynccontextmanager
    async def session():
        """借用同一假的任务事务观察两次短生命周期执行。"""
        yield db

    prepared = FragmentVideoPrepared(
        submit_mode="seedance_body", prompt="Base draft", duration=8, ratio="9:16", resolution="720p",
        model_id="planned", seedance_body={"model": "actual-model", "duration": 6, "ratio": "16:9",
        "resolution": "480p", "generate_audio": False,
        "content": [{"type": "text", "text": "Final generated prompt"},
                    {"type": "image_url", "image_url": {"url": "https://private.test/reference.png"}}]},
    )
    monkeypatch.setattr(jobs, "AsyncSessionLocal", session)
    monkeypatch.setattr(jobs, "_is_episode_video_cancelled", lambda _: False)
    monkeypatch.setattr(jobs, "prepare_fragment_video_for_submit", AsyncMock(return_value=prepared))
    monkeypatch.setattr(jobs, "submit_prepared_fragment_video", AsyncMock(return_value="provider-5"))
    monkeypatch.setattr(service, "get_task_for_runtime", AsyncMock(return_value=task))
    monkeypatch.setattr(service, "append_task_event", AsyncMock())

    assert (await jobs.submit_fragment_video_task(task))["nio_phase"] == "submit"
    assert task.payload["generation_params"]["prompt"] == "Final generated prompt"
    assert (await jobs.submit_fragment_video_task(task))["provider_task_id"] == "provider-5"
    assert "prepared" not in task.payload
    snapshot = task.payload["generation_params"]
    assert snapshot["model_id"] == "actual-model" and snapshot["duration"] == 6
    assert snapshot["aspect_ratio"] == "16:9" and snapshot["generate_audio"] is False
    assert "private.test" not in str(snapshot)


def test_output_metadata_probes_media_duration_and_clears_previous_measurements(tmp_path, monkeypatch):
    """成片时长来自 ffprobe；探测失败不沿用前一次视频的时长和像素。"""
    video = tmp_path / "output.mp4"
    video.write_bytes(b"mocked-video")
    fragment = SimpleNamespace(params={"generation": {"status": "running"}})
    monkeypatch.setattr("app.services.ffmpeg_compose.probe_video_dimensions", lambda _: (854, 480))
    monkeypatch.setattr("app.services.ffmpeg_compose.probe_duration", lambda _: 8.04)
    write_fragment_video_output_meta(fragment, aspect_ratio="9:16", resolution="480p", video_path=video)
    assert fragment.params["video_duration_sec"] == 8.04
    assert fragment.params["generation"]["video_width"] == 854

    monkeypatch.setattr("app.services.ffmpeg_compose.probe_video_dimensions", lambda _: None)
    monkeypatch.setattr("app.services.ffmpeg_compose.probe_duration", lambda _: None)
    write_fragment_video_output_meta(fragment, aspect_ratio="9:16", resolution="720p", video_path=video)
    assert "video_duration_sec" not in fragment.params
    assert "video_width" not in fragment.params["generation"]


async def test_task_completion_freezes_metadata_for_the_matching_output(monkeypatch):
    """完成时复制本次视频的实测信息，历史详情不再依赖可变分镜。"""
    from app.services.billing import settlement
    from app.services.drama import jobs
    from app.services.tasks import executor, service

    task = TaskRun(id=42, status="awaiting_poll", payload={}, steps=[])
    fragment = SimpleNamespace(video="https://output.test/current.mp4", params={"generation": {
        "video": "https://output.test/current.mp4", "video_duration_sec": 8.04, "video_width": 854, "video_height": 480}})
    db = SimpleNamespace(get=AsyncMock(return_value=fragment), refresh=AsyncMock())
    complete = AsyncMock()
    monkeypatch.setattr(settlement, "_lock_task", AsyncMock(return_value=task))
    monkeypatch.setattr(executor, "_complete_task", complete)
    monkeypatch.setattr(service, "activate_next_sequential_task", AsyncMock())
    assert await jobs._recover_complete_fragment_video(db, 42, fragment_id=9, batch_key=None, batch_index=0)
    result = complete.await_args.args[2]
    assert result["media_info"] == {"duration_sec": 8.04, "width": 854, "height": 480}
    fragment.params["generation"]["video_duration_sec"] = 20
    assert result["media_info"]["duration_sec"] == 8.04

    fragment.params["generation"]["video"] = "https://output.test/previous.mp4"
    await jobs._recover_complete_fragment_video(db, 42, fragment_id=9, batch_key=None, batch_index=0)
    assert "media_info" not in complete.await_args.args[2]
