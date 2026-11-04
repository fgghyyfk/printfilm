"""隐私备用配音只生成本镜，缓存不误认共享整片音轨。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.services.tts_billing import TTSResult
from app.services import kepu_privacy_audio as fallback
from app.services import pipeline, storage
from tests.test_kepu_task_recovery import seed_project
from tests.test_kepu_privacy_skip import _same_session


async def test_privacy_fallback_keeps_actual_tts_once_and_changes_cache_with_text(db_session, tmp_path, monkeypatch):
    user, project, shot = await seed_project(db_session)
    project.pipeline_mode = "full"
    shot.video_skip_reason = "privacy"
    shot.audio_url = "/static/shared/full_narration.mp3"
    await db_session.commit()
    source = tmp_path / "tts.mp3"
    source.write_bytes(b"controlled audible audio")
    ark = SimpleNamespace(tts_with_result=AsyncMock(return_value=TTSResult(str(source), 'edge', 'controlled-edge', True)))
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", True)
    monkeypatch.setattr(fallback, "AsyncSessionLocal", lambda: _same_session(db_session))
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock())
    monkeypatch.setattr(pipeline, "_project_voice", lambda _: "voice-a")
    monkeypatch.setattr("app.services.ark.get_ark", lambda: ark)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "local_path_from_url", lambda url: __import__("pathlib").Path(url))
    monkeypatch.setattr(storage, "publish_local", str)
    monkeypatch.setattr(fallback, "is_near_silent_audio", lambda _: False)
    await fallback.ensure_privacy_audio(project.id, shot.id)
    first = shot.audio_url
    assert first != "/static/shared/full_narration.mp3"
    assert shot.tts_metadata['provider'] == 'edge'
    assert shot.tts_metadata['is_fallback'] is True
    assert shot.tts_metadata['audio_url'] == first
    await fallback.ensure_privacy_audio(project.id, shot.id)
    assert ark.tts_with_result.await_count == 1
    shot.narration = "改过的本镜旁白"
    await db_session.commit()
    await fallback.ensure_privacy_audio(project.id, shot.id)
    assert shot.audio_url != first
    assert ark.tts_with_result.await_count == 2
    assert ark.tts_with_result.call_args.kwargs["billing_shot_id"] == shot.id
    shot.video_url = "/static/native-with-voice.mp4"
    await db_session.commit()
    await fallback.ensure_privacy_audio(project.id, shot.id)
    assert ark.tts_with_result.await_count == 2
