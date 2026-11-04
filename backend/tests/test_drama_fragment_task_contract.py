"""队列版本契约及媒体归档短锁边界。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaEpisodeFragment
from app.services.tasks.handlers import _run_drama_fragment_plan
from tests.test_drama_fragment_save import committed_episode, item, make_episode, request


@pytest.mark.parametrize('revision', [None, True, -1, '1', 1.5])
async def test_queued_plan_rejects_missing_or_invalid_revision(monkeypatch, revision):
    """缺失/非法队列版本必须在调用 LLM worker 前失败，禁止借用当前版本。"""
    worker = AsyncMock()
    monkeypatch.setattr('app.services.drama.jobs.run_episode_fragment_plan_job', worker)
    payload = {'force': True}
    if revision is not None:
        payload['expected_revision'] = revision
    with pytest.raises(ValueError, match='内容已在别处更新，请刷新'):
        await _run_drama_fragment_plan(SimpleNamespace(episode_id=1, payload=payload))
    worker.assert_not_awaited()


async def test_old_queued_plan_without_revision_cannot_overwrite_pre_start_edits(db_session, monkeypatch):
    """模拟升级前已排队的任务，用户保存后再启动也不能重设版本基线。"""
    from app.api.drama.episodes import save_fragments
    user, _, ep, rows = await make_episode(db_session)
    queued = SimpleNamespace(episode_id=ep.id, payload={'force': True})
    await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'new'})]), db_session, user)
    llm = AsyncMock()
    monkeypatch.setattr('app.services.drama.fragment_plan.plan_fragments_with_llm', llm)
    with pytest.raises(ValueError, match='内容已在别处更新，请刷新'):
        await _run_drama_fragment_plan(queued)
    llm.assert_not_awaited()
    await db_session.refresh(rows[0])
    assert rows[0].params['user_setting'] == 'new'


async def test_history_media_snapshot_does_not_hold_episode_lock(committed_episode, monkeypatch):
    """旧媒体快照可能下载远端资源，该边界必须允许其它连接取得分集锁。"""
    from app.services.drama.generation import apply_fragment_video_assets
    engine, user, project, ep, rows = committed_episode
    sync_engine = create_engine(engine.url.set(drivername='postgresql+psycopg2'))
    async with AsyncSession(engine) as setup:
        row = await setup.get(DramaEpisodeFragment, rows[0].id)
        row.video, row.cover = 'https://old/video', 'https://old/cover'
        row.params = {'lastFrameUrl': 'https://old/tail'}
        await setup.commit()
    def snapshot(url, **kwargs):
        """真实第二连接 NOWAIT 验证网络快照边界没有占分集行锁。"""
        with sync_engine.begin() as conn:
            conn.execute(text('SELECT id FROM drama_episodes WHERE id=:id FOR UPDATE NOWAIT'), {'id': ep.id})
        return url
    monkeypatch.setattr('app.services.drama.generation._snapshot_version_media_url', snapshot)
    monkeypatch.setattr('app.services.drama.generation.record_seedance_video_usage', AsyncMock())
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda url: None)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            row = await db.get(DramaEpisodeFragment, rows[0].id)
            await apply_fragment_video_assets(db, user, project, row, local_video='https://new/video', local_last_frame='https://new/tail')
            assert row.params['video_versions'][0]['video'] == 'https://old/video'
    finally:
        sync_engine.dispose()


async def test_enqueue_generation_preserves_edit_saved_during_queue_lookup(committed_episode, monkeypatch):
    """生成入队的计数等待期间发生保存，queued 写入只更新运行态。"""
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.api.drama.episodes import generate_episode, save_fragments
    from app.models_drama import DramaProject
    from app.schemas_drama import DramaGenerateRequest
    engine, user, project, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        current = await db.get(DramaProject, project.id)
        current.params = {'linkLastFrame': False}
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    async def count(*args):
        """阻塞只读计数，不模拟分镜实际写入。"""
        entered.set()
        await release.wait()
        return 0
    monkeypatch.setattr('app.api.drama.episodes.count_user_inflight_fragment_video_tasks', count)
    monkeypatch.setattr('app.api.drama.episodes.create_task', AsyncMock(return_value=SimpleNamespace(id=123)))
    async def enqueue():
        """使用真实生成路由写 queued 参数。"""
        async with factory() as db:
            return await generate_episode(ep.id, DramaGenerateRequest(fragment_ids=[rows[0].id]), db, user)
    worker = asyncio.create_task(enqueue())
    await asyncio.wait_for(entered.wait(), 5)
    async with factory() as db:
        await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'during-count'})]), db, user)
    release.set()
    await asyncio.wait_for(worker, 5)
    async with factory() as db:
        row = await db.get(DramaEpisodeFragment, rows[0].id)
        assert row.params['user_setting'] == 'during-count'
        assert row.params['generation']['status'] == 'queued'


@pytest.mark.parametrize('network_stage', ['usage', 'pricing'])
async def test_billing_network_preparation_does_not_hold_episode_lock(committed_episode, monkeypatch, network_stage):
    """缺 token 的上游补查和冷价目加载都必须在分集锁外完成。"""
    from sqlalchemy import delete, select
    from sqlalchemy.exc import DBAPIError
    from app.models import UsageEvent
    from app.models_drama import DramaEpisode
    from app.services.ark import TaskResult
    from app.services.drama.generation import apply_fragment_video_assets
    engine, user, project, ep, rows = committed_episode
    observed = []
    async def check_lock():
        """另一个真实连接 NOWAIT 判定外部请求期间是否占用分集锁。"""
        async with AsyncSession(engine) as probe:
            try:
                await probe.scalar(select(DramaEpisode.id).where(DramaEpisode.id == ep.id).with_for_update(nowait=True))
                observed.append(True)
            except DBAPIError:
                observed.append(False)
    async def fetch(*args):
        """模拟补查上游 token，只验证锁状态。"""
        if network_stage == 'usage':
            await check_lock()
        return TaskResult(status='succeeded', total_tokens=100_000, raw_usage={'total_tokens': 100_000})
    async def rates(*args):
        """模拟冷缓存价目请求，计算仍走既有算法。"""
        if network_stage == 'pricing':
            await check_lock()
        return {}
    monkeypatch.setattr('app.services.ark.get_ark', lambda: SimpleNamespace(fetch_task_once=fetch))
    monkeypatch.setattr('app.services.tokenfree_pricing.ensure_official_rates', rates)
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda url: None)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            row = await db.get(DramaEpisodeFragment, rows[0].id)
            await apply_fragment_video_assets(db, user, project, row, local_video='https://new/video', local_last_frame='https://new/tail', task_result=TaskResult(status='succeeded'), provider_task_id='local-test')
            assert observed and all(observed)
            event = (await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id))).one()
            assert event.total_tokens == 100_000
            assert event.cost_fen > 0
    finally:
        async with AsyncSession(engine) as db:
            await db.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
            await db.commit()


async def test_runtime_json_patch_ignores_dirty_cached_params_and_flushes_pending_task(committed_episode):
    """旧对象已有未 flush 的整列 JSON 时，只允许明确 runtime patch 落库。"""
    from sqlalchemy import delete
    from app.models_tasks import TaskRun
    from app.api.drama.episodes import save_fragments
    from app.services.drama.fragment_editing import patch_fragment_params
    engine, user, _, ep, rows = committed_episode
    task_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as stale_db:
            cached = await stale_db.get(DramaEpisodeFragment, rows[0].id)
            cached.params = {'user_setting': 'dirty-stale', 'video_versions': []}
            async with AsyncSession(engine, expire_on_commit=False) as save_db:
                await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'fresh'})]), save_db, user)
            task = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id, status='failed')
            stale_db.add(task)
            await patch_fragment_params(stale_db, cached, {'generation': {'status': 'failed'}})
            await stale_db.commit()
            task_id = task.id
            assert task_id is not None
            assert cached.params == {'user_setting': 'fresh', 'generation': {'status': 'failed'}}
    finally:
        async with AsyncSession(engine) as db:
            await db.execute(delete(TaskRun).where(TaskRun.requested_by == user.id))
            await db.commit()
