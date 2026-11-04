"""真实提交参考快照不会在素材换图或历史切换时冒充最新版本。"""
from unittest.mock import AsyncMock

from app.models_drama import DramaAsset, DramaFragmentAssetRef
from app.services.drama import generation
from app.services.drama.asset_image_revision import bump_asset_image_revision
from tests.test_drama_fragment_save import make_episode


async def test_prepare_snapshot_survives_image_change_result_and_history(db_session, monkeypatch):
    user, project, episode, rows = await make_episode(db_session)
    project.params = {'linkLastFrame': False}
    asset = DramaAsset(project_id=project.id, type='character', asset_type='image', cover='https://test/old.png')
    bump_asset_image_revision(asset)
    db_session.add(asset)
    await db_session.flush()
    db_session.add(DramaFragmentAssetRef(fragment_id=rows[0].id, asset_id=asset.id))
    await db_session.commit()
    monkeypatch.setattr(generation, 'SEEDANCE_ATTACH_REFERENCE_AUDIO', False)
    monkeypatch.setattr(generation, 'ensure_reference_assets_public_urls', AsyncMock(return_value=[asset]))
    monkeypatch.setattr(generation, 'resolve_image_style_board_url', lambda _: '')
    monkeypatch.setattr('app.services.drama.voice_reference_audio.validate_seedance_body_reference_audios', AsyncMock())
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda _: None)
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **_: url)
    monkeypatch.setattr(generation, 'prepare_seedance_video_usage', AsyncMock(return_value=None))
    monkeypatch.setattr(generation, 'record_seedance_video_usage', AsyncMock())
    monkeypatch.setattr(generation, '_snapshot_version_media_url', lambda url, **_: url)
    prepared = await generation.prepare_fragment_video_for_submit(db_session, user, project, rows[0])
    serialized = generation.serialize_fragment_video_prepared(prepared)
    snapshot = serialized['reference_snapshot']
    input_snapshot = serialized['video_input_snapshot']
    assert input_snapshot['content'] == rows[0].content
    old_revision = asset.image_revision
    # 同路径覆盖也旋转；之后完成的旧任务只能携带实际提交的旧版本。
    bump_asset_image_revision(asset)
    assert asset.image_revision != old_revision
    await db_session.commit()
    await generation.apply_fragment_video_assets(db_session, user, project, rows[0], local_video='https://test/old.mp4', reference_snapshot=snapshot, video_input_snapshot=input_snapshot)
    assert rows[0].params['reference_snapshot'][0]['revision'] == old_revision
    generation.archive_fragment_video_version(rows[0])
    version = rows[0].params['video_versions'][0]
    rows[0].video = 'https://test/new.mp4'
    rows[0].params = {**rows[0].params, 'reference_snapshot': [{**snapshot[0], 'revision': asset.image_revision}]}
    result = generation.activate_fragment_video_version(rows[0], version['id'])
    assert result['reference_snapshot'] == snapshot
    assert result['video_input_snapshot'] == input_snapshot
    assert rows[0].params['video_versions'][0]['reference_snapshot'][0]['revision'] == asset.image_revision


async def test_quote_rejects_changed_output_without_relying_on_episode_revision(db_session, monkeypatch):
    from fastapi import HTTPException
    import pytest
    from app.api.drama.episodes import generate_episode
    from app.schemas_drama import DramaGenerateRequest
    from app.services.drama.fragment_video_quote import fragment_video_task_inputs

    user, project, episode, rows = await make_episode(db_session)
    project.params = {'linkLastFrame': False}
    episode.params = {'aspect_ratio': '9:16', 'resolution': '480p'}
    inputs = fragment_video_task_inputs(episode, project, rows[0], 'seedance-2.5')
    quoted = {'fragment_id': rows[0].id, **{key: inputs[key] for key in ('duration_sec', 'model_id', 'resolution', 'aspect_ratio')}}
    episode.params = {**episode.params, 'resolution': '720p'}
    await db_session.commit()
    create = AsyncMock()
    monkeypatch.setattr('app.api.drama.episodes.create_task', create)
    body = DramaGenerateRequest(fragment_ids=[rows[0].id], model_id='seedance-2.5', quoted_inputs=[quoted])
    with pytest.raises(HTTPException) as exc:
        await generate_episode(episode.id, body, db_session, user)
    assert exc.value.status_code == 409
    assert '重新报价' in exc.value.detail
    create.assert_not_awaited()
