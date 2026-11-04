"""配音媒体不可变，试听按真实来源和场景缓存。"""
from pathlib import Path
from unittest.mock import AsyncMock

from app.config import Settings
from app.services.ark import ArkGateway


async def test_same_project_and_shot_ids_never_overwrite_previous_audio(tmp_path, monkeypatch):
    gateway = ArkGateway(settings=Settings(ark_mock=False, ark_api_key='', ark_base_url='https://unused.test'))
    monkeypatch.setattr('app.services.storage.project_dir', lambda _: tmp_path)
    monkeypatch.setattr('app.services.storage.publish_local', lambda path: str(path))
    monkeypatch.setattr('app.services.ark.is_near_silent_audio', lambda _: False)
    monkeypatch.setattr('app.services.volc_tts.is_configured', lambda: False)
    monkeypatch.setattr(gateway, '_tts_openai_speech', AsyncMock(return_value=False))
    async def edge(text, dest, **kwargs):
        dest.write_bytes(text.encode() * 4000)
    monkeypatch.setattr(gateway, '_tts_edge', edge)
    first = await gateway.tts_with_result('A', 'narrator_calm', project_id=7, shot_no=1)
    second = await gateway.tts_with_result('B', 'narrator_calm', project_id=7, shot_no=1)
    assert first.url != second.url
    assert Path(first.url).read_bytes() == b'A' * 4000
    assert Path(second.url).read_bytes() == b'B' * 4000


async def test_preview_fallback_is_not_cached_and_scene_route_are_isolated(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services import voices, storage
    from app.services.tts_billing import TTSResult

    calls = []
    route = ['volc', 'volc/controlled']
    async def synthesize(text, voice, **kwargs):
        calls.append((text, voice))
        path = tmp_path / f'sample-{len(calls)}.mp3'
        path.write_bytes(str(len(calls)).encode() * 4000)
        if len(calls) == 1:
            return TTSResult(str(path), 'edge', 'controlled-edge', True)
        return TTSResult(str(path), *route)
    monkeypatch.setattr(storage, 'STATIC_ROOT', tmp_path)
    monkeypatch.setattr(storage, 'local_path_from_url', Path)
    monkeypatch.setattr(storage, 'publish_local', str)
    monkeypatch.setattr('app.services.ark.get_ark', lambda: SimpleNamespace(tts_with_result=synthesize))
    monkeypatch.setattr(voices, '_preview_primary_route', lambda *_: tuple(route))
    first = await voices.ensure_voice_preview('narrator_calm', scene='daihuo')
    assert first.is_fallback
    assert not list((tmp_path / 'voice_previews').glob('*.mp3'))
    preferred = await voices.ensure_voice_preview('narrator_calm', scene='daihuo')
    cached = await voices.ensure_voice_preview('narrator_calm', scene='daihuo')
    assert cached.url == preferred.url
    assert len(calls) == 2
    other_scene = await voices.ensure_voice_preview('narrator_calm', scene='drama')
    assert other_scene.url != preferred.url and calls[1][0] != calls[2][0]
    route[:] = ['tokenfree', 'controlled-other-model']
    other_route = await voices.ensure_voice_preview('narrator_calm', scene='daihuo')
    assert other_route.url != preferred.url and len(calls) == 4


async def test_continuous_audio_records_all_affected_shots_and_cache_keeps_actual_source(db_session, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.models import Shot
    from app.services import pipeline, storage
    from app.services.tts_billing import TTSResult
    from tests.test_kepu_task_recovery import seed_project
    from tests.test_kepu_privacy_skip import _same_session

    _, project, first = await seed_project(db_session)
    second = Shot(project_id=project.id, shot_no=2, narration='第二段')
    db_session.add(second)
    await db_session.commit()
    source = tmp_path / 'immutable.mp3'
    source.write_bytes(b'a' * 4000)
    synthesize = AsyncMock(return_value=TTSResult(str(source), 'volc', 'volc/actual'))
    monkeypatch.setattr(pipeline, 'get_ark', lambda: SimpleNamespace(tts_with_result=synthesize))
    monkeypatch.setattr(pipeline, 'AsyncSessionLocal', lambda: _same_session(db_session))
    monkeypatch.setattr(storage, 'project_dir', lambda _: tmp_path)
    monkeypatch.setattr(storage, 'local_path_from_url', Path)
    monkeypatch.setattr(storage, 'publish_local', str)
    monkeypatch.setattr(pipeline, '_continuous_audio_ok', lambda _: True)
    monkeypatch.setattr(pipeline, 'is_near_silent_audio', lambda _: False)
    monkeypatch.setattr(pipeline, 'probe_duration', lambda _: 8.0)
    await pipeline._synthesize_continuous_audio(project.id, voice='voice-a', shot_rows=[first, second])
    for shot in [first, second]:
        assert shot.audio_url == str(source)
        assert shot.tts_metadata['provider'] == 'volc'
        assert shot.tts_metadata['audio_url'] == shot.audio_url
    await pipeline._synthesize_continuous_audio(project.id, voice='voice-after-config-change', shot_rows=[first, second])
    assert synthesize.await_count == 1
    assert first.tts_metadata['provider'] == 'volc'
    # 升级前缓存来源未知，命中时不按当前配置伪造来源。
    first.tts_metadata = None
    await db_session.commit()
    await pipeline._synthesize_continuous_audio(project.id, voice='voice-a', shot_rows=[first, second])
    assert first.tts_metadata is None and synthesize.await_count == 1
