"""套图复用的报价/新项目只读与幂等边界。"""
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from app.models import Project, Template
from app.models_ecom import EcomSet, EcomSetItem
from app.models_tasks import TaskRun
from app.services.daihuo import service
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID
from tests.conftest import make_user


async def seed_set(db, tmp_path):
    user = await make_user(db)
    if not await db.get(Template, DAIHUO_TEMPLATE_ID):
        db.add(Template(id=DAIHUO_TEMPLATE_ID, name='带货模板', style_prefix=''))
    ecom = EcomSet(user_id=user.id, name='已有套图', status='partial')
    db.add(ecom)
    await db.flush()
    for idx in range(3):
        path = tmp_path / f'{idx}.jpg'
        if idx < 2:
            path.write_bytes(b'existing image')
        db.add(EcomSetItem(set_id=ecom.id, idx=idx, status='succeeded', base_path=str(path)))
    await db.commit()
    await db.refresh(ecom)
    return user, ecom


async def test_quote_uses_only_real_usable_images_without_creating_project_or_task(db_session, tmp_path, monkeypatch):
    user, ecom = await seed_set(db_session, tmp_path)
    before = (await db_session.scalar(select(func.count(Project.id))), await db_session.scalar(select(func.count(TaskRun.id))))
    monkeypatch.setattr('app.services.billing.settlement.billing_active', lambda *_: True)
    monkeypatch.setattr('app.services.tokenfree_pricing.ensure_official_rates', AsyncMock())
    observed = []
    def estimate(project, phase, settings):
        observed.append((phase, len(project.shots), [row.image_url for row in project.shots]))
        return 11 if phase == 'script' else len(project.shots) * 5
    monkeypatch.setattr('app.services.billing.estimates.estimate_phase_fen', estimate)
    quote = await service.quote_project_from_set(db_session, user, ecom.id, output_ratio='16:9', voice_id='voice-b')
    after = (await db_session.scalar(select(func.count(Project.id))), await db_session.scalar(select(func.count(TaskRun.id))))
    assert quote['usable_images'] == 2 and quote['image_fen'] == 0
    assert quote['script_fen'] == 11 and quote['tts_fen'] == 10
    assert quote['output_ratio'] == '16:9' and quote['voice_id'] == 'voice-b'
    assert before == after and ecom.project_id is None
    assert all(count == 2 and all(Path(path).exists() for path in paths) for _, count, paths in observed)


async def test_explicit_second_video_keeps_original_and_retries_same_request(db_session, tmp_path):
    user, ecom = await seed_set(db_session, tmp_path)
    first = await service.create_project_from_set(db_session, user, ecom.id, output_ratio='9:16', voice_id='voice-a')
    # 实际旧入口忽略换画幅/音色，始终返回旧项目；新建必须由明确动作授权。
    legacy = await service.create_project_from_set(db_session, user, ecom.id, output_ratio='16:9', voice_id='voice-b')
    assert legacy.id == first.id and legacy.voice_id == 'voice-a' and legacy.output_ratio == '9:16'
    second = await service.create_project_from_set(db_session, user, ecom.id, output_ratio='16:9', voice_id='voice-b',
                                                  create_new=True, request_id='controlled-second-video')
    retry = await service.create_project_from_set(db_session, user, ecom.id, output_ratio='16:9', voice_id='voice-b',
                                                 create_new=True, request_id='controlled-second-video')
    assert second.id != first.id and retry.id == second.id
    assert first.voice_id == 'voice-a' and first.output_ratio == '9:16'
    assert second.voice_id == 'voice-b' and second.output_ratio == '16:9'
    assert ecom.project_id == second.id
    assert (await db_session.scalar(select(func.count(Project.id)).where(Project.ecom_set_id == ecom.id))) == 2
