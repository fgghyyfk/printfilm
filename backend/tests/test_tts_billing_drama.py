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


async def test_explicit_drama_voice_creates_task_and_settles_once(db_session, monkeypatch, tmp_path):
    """显式音色先提交预扣，再独立记账结算，资产 helper 不提交业务事务。"""
    from app.api.drama.generation import generate_voice
    from app.schemas_drama import DramaVoiceGenerateRequest
    from app.services.drama import voice_synthesis
    user, project, asset = await _drama_voice(db_session)
    gateway = _gateway(monkeypatch, tmp_path)
    _http(monkeypatch, lambda request: _volc_audio())
    monkeypatch.setattr(voice_synthesis, "get_ark", lambda: gateway)
    monkeypatch.setattr(voice_synthesis, "finalize_voice_reference_url", lambda url, **kwargs: url)
    original_commit = db_session.commit
    commits = []

    async def tracked_commit():
        """所有 commit 都必须位于 billing_scope 外，结算由外围完成。"""
        from app.services.billing.context import get_current_task_run_id
        commits.append(get_current_task_run_id())
        await original_commit()
    monkeypatch.setattr(db_session, "commit", tracked_commit)
    result = await generate_voice(DramaVoiceGenerateRequest(
        project_id=project.id, asset_id=asset.id, voice_prompt="清晰的青年男声", speaker="narrator_calm",
    ), db=db_session, user=user)
    task = await db_session.get(TaskRun, result["task_id"])
    rows = await _events(db_session, task)
    assert task.domain == "drama" and task.task_type == "voice_synthesis"
    assert task.status == "succeeded" and task.billing_status == "settled"
    assert len(rows) == 1
    assert rows[0].provider == "volc" and rows[0].drama_project_id == project.id
    assert rows[0].settled is True
    assert task.billing_charged_fen == rows[0].charge_fen > 0
    assert int(user.balance_fen) == 100_000 - task.billing_charged_fen
    assert int(user.frozen_fen) == 0
    assert commits and all(value is None for value in commits)


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


async def test_explicit_drama_voice_insufficient_balance_does_not_call_upstream(db_session, monkeypatch, tmp_path):
    """显式音色合成用现有估价校验余额，预扣不足返回 402 且不调用上游。"""
    from fastapi import HTTPException
    from app.api.drama.generation import generate_voice
    from app.schemas_drama import DramaVoiceGenerateRequest
    from app.services.drama import voice_synthesis
    user, project, asset = await _drama_voice(db_session)
    user.balance_fen = 0
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(voice_synthesis, "get_ark", lambda: gateway)
    _http(monkeypatch, lambda request: pytest.fail("余额不足不应请求付费接口"))
    with pytest.raises(HTTPException) as info:
        await generate_voice(DramaVoiceGenerateRequest(
            project_id=project.id, asset_id=asset.id, voice_prompt="青年男声",
        ), db=db_session, user=user)
    assert info.value.status_code == 402
    assert user.balance_fen == 0 and user.frozen_fen == 0


