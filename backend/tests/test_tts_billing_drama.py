"""显式音色与已有分镜参考音的任务、钱包及事务归属。"""
from contextlib import asynccontextmanager

import pytest
from tests.tts_database import db_session

from app.models_drama import DramaProject, DramaAsset
from app.models_tasks import TaskRun
from app.services.billing.context import billing_scope
from app.services.tasks.upstream import upstream_event_session
from tests.conftest import make_task, make_user
from tests.test_tts_billing import _gateway, _http, _volc_audio, _events


async def _drama_voice(db):
    """建立漫剧真实音色资产。"""
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title="音色测试")
    db.add(project)
    await db.flush()
    asset = DramaAsset(project_id=project.id, type="voice", asset_type="audio", name="角色音色")
    db.add(asset)
    await db.flush()
    await db.commit()
    return user, project, asset


async def test_reference_voice_uses_existing_task_and_settles_once(db_session, monkeypatch, tmp_path):
    """分镜自动参考音在既有 TaskRun 内唯一记账，不创建额外音色任务。"""
    from app.services.drama import voice_synthesis
    from app.services.billing import settle_task
    user, project, asset = await _drama_voice(db_session)
    task = await make_task(db_session, user, domain="drama", task_type="fragment_video")
    task.drama_project_id = project.id
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    monkeypatch.setattr(voice_synthesis, "get_ark", lambda: gateway)
    monkeypatch.setattr(voice_synthesis, "finalize_voice_reference_url", lambda url, **kwargs: url)

    await db_session.commit()
    async with billing_scope(task.id), upstream_event_session(db_session):
        await voice_synthesis.synthesize_voice_asset(
            db_session, user, project, asset, voice_prompt="青年男声", speaker="narrator_calm")
    await settle_task(db_session, task.id)
    rows = await _events(db_session, task)
    assert len(rows) == 1, [(row.billing_key, row.provider) for row in rows]
    assert rows[0].provider == "volc" and rows[0].drama_project_id == project.id
    assert rows[0].settled is True
    assert task.billing_charged_fen == rows[0].charge_fen > 0
    assert int(user.balance_fen) == 100_000 - task.billing_charged_fen


async def test_reference_local_failure_does_not_rollback_paid_usage(db_session, monkeypatch, tmp_path):
    """已有分镜任务的参考音成本不能随资产事务失败回滚消失。"""
    from app.services.drama import voice_synthesis
    from app.services import tts_billing
    user, project, asset = await _drama_voice(db_session)
    task = await make_task(db_session, user, domain="drama", task_type="fragment_video")
    task.drama_project_id = project.id
    await db_session.commit()
    task_id = task.id
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    monkeypatch.setattr(voice_synthesis, "get_ark", lambda: gateway)

    def fail_finalize(*args, **kwargs):
        """音频已成功，资产本地处理随后失败。"""
        raise RuntimeError("local reference failure")
    monkeypatch.setattr(voice_synthesis, "finalize_voice_reference_url", fail_finalize)

    async with billing_scope(task_id):
        with pytest.raises(RuntimeError, match="local reference"):
            await voice_synthesis.synthesize_voice_asset(
                db_session, user, project, asset, voice_prompt="青年男声", speaker="narrator_calm")
    await db_session.rollback()
    task = await db_session.get(TaskRun, task_id)
    rows = await _events(db_session, task)
    assert len(rows) == 1 and rows[0].provider == "volc"
