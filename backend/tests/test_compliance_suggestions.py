"""逐处建议沿原稿码点定位，嵌套词去重且旧扫描不能覆盖新稿。"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.api.projects import update_shot
from app.models import Shot
from app.schemas import ShotUpdate
from app.services.compliance.ad_words import report_hits
from app.services.compliance.content import scan_fragments, scan_shots
from app.services.compliance.suggestions import prepare_suggestion
from tests.test_kepu_task_recovery import seed_project


def command(item, hit):
    return {'compliance_apply': {'field': item['field'], 'text_hash': item['text_hash'],
            'start': hit['start'], 'end': hit['end'], 'word': hit['word'], 'suggestion': hit['suggestion']}}


def test_report_has_each_real_span_and_safe_suggestion_without_nested_overcount():
    text = '😀最好用，最好用真丝'
    hits = report_hits(text)
    assert [hit.word for hit in hits] == ['最好用', '最好用', '真丝']
    assert [hit.suggestion for hit in hits] == ['好用', '好用', None]
    assert all(text[hit.start:hit.end] == hit.word for hit in hits)


def test_drama_spans_point_to_raw_body_preserving_emoji_crlf_and_speaker():
    text = '@duration:8\r\n出场人物：最好用、小美\r\n【对白】最好用（vo）：😀最好用，最好用\r\n'
    item = scan_fragments([SimpleNamespace(id=1, content=text)])[0]
    assert len(item['hits']) == 2
    first, second = item['hits']
    assert first['start'] == text.index('😀') + 1
    assert text[second['start']:second['end']] == '最好用'
    result = text[:second['start']] + second['suggestion'] + text[second['end']:]
    assert result.startswith('@duration:8\r\n出场人物：最好用、小美\r\n【对白】最好用（vo）：😀最好用，')
    assert result.endswith('好用\r\n')


def test_apply_narration_maps_only_actual_spoken_occurrence_in_related_scripts():
    script = '@duration:4\r\n【画面】最好用显示\r\n【旁白】😀最好用和顶级\r\n'
    shot = SimpleNamespace(id=1, shot_no=1, narration='😀最好用和顶级', segment_script=script, video_prompt=script)
    item = next(item for item in scan_shots([shot]) if item['field'] == 'narration')
    data, applied = prepare_suggestion(shot, command(item, item['hits'][0]), allowed={'narration', 'segment_script', 'video_prompt'})
    assert applied and data['narration'] == '😀好用和顶级'
    assert data['segment_script'] == script.replace('【旁白】😀最好用', '【旁白】😀好用')
    assert data['video_prompt'] == data['segment_script']


async def test_stale_report_checks_fresh_locked_text_before_any_media_change(db_session):
    user, project, shot = await seed_project(db_session)
    shot.narration = '😀最好用'
    shot.audio_url, shot.video_url = '/static/audio.mp3', '/static/video.mp4'
    await db_session.commit()
    item = next(item for item in scan_shots([shot]) if item['field'] == 'narration')
    await db_session.execute(update(Shot).where(Shot.id == shot.id).values(narration='刚保存的新稿').execution_options(synchronize_session=False))
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await update_shot(project.id, shot.id, ShotUpdate.model_validate(command(item, item['hits'][0])), db_session, user)
    assert exc.value.status_code == 409
    await db_session.refresh(shot)
    assert shot.narration == '刚保存的新稿'
    assert shot.audio_url == '/static/audio.mp3' and shot.video_url == '/static/video.mp4'
