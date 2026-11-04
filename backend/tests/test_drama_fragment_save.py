"""分镜编辑版本、显式删除与异步回填的 PostgreSQL 回归。"""
import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.drama.episodes import save_fragments
from app.models import UsageEvent, User
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from app.schemas_drama import DramaSaveFragmentsRequest
from app.services.billing.usage import prepare_usage_line
from tests.conftest import make_user


async def make_episode(db):
    """创建只属于本用例的两镜分集。"""
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title='test')
    db.add(project)
    await db.flush()
    episode = DramaEpisode(project_id=project.id, name='test', params={'episodeNumber': 1})
    db.add(episode)
    await db.flush()
    rows = [DramaEpisodeFragment(episode_id=episode.id, sort_order=i, content=f'原稿{i}', params={'user_setting': 'old'}) for i in range(2)]
    db.add_all(rows)
    await db.flush()
    return user, project, episode, rows


def request(rows=(), *, revision=0, deleted_ids=()):
    """构造带编辑快照版本的保存请求。"""
    return DramaSaveFragmentsRequest.model_validate({'revision': revision, 'deleted_ids': list(deleted_ids), 'fragments': list(rows)})


def item(row, **kwargs):
    """只提交用户实际编辑字段。"""
    return {'id': row.id, 'content': '用户修改', 'sort_order': row.sort_order, **kwargs}


@pytest.mark.parametrize('client_params', [{}, {'user_setting': 'new', 'video_versions': [], 'lastFrameUrl': 'fake', 'generation_attempts': 0, 'generation': {'status': 'idle'}}])
async def test_save_keeps_server_media_and_runtime_params(db_session, client_params):
    """过期客户端不能抹掉视频、尾帧、版本及重试状态。"""
    user, _, ep, rows = await make_episode(db_session)
    row = rows[0]
    row.video, row.cover = 'https://server/new.mp4', 'https://server/new.jpg'
    server = {'video_versions': [{'id': 'old'}], 'lastFrameUrl': 'tail', 'last_frame_url': 'tail2', 'generation': {'status': 'done'}, 'generation_attempts': 2}
    row.params = {'user_setting': 'old', 'other_setting': 1, **server}
    await db_session.commit()
    out = await save_fragments(ep.id, request([item(row, params=client_params, video='stale', cover='stale')]), db_session, user)
    saved = next(f for f in out.fragments if f.id == row.id)
    assert saved.video == 'https://server/new.mp4'
    assert saved.cover == 'https://server/new.jpg'
    assert {key: saved.params[key] for key in server} == server
    assert saved.params['other_setting'] == 1
    assert saved.params['user_setting'] == client_params.get('user_setting', 'old')
    assert out.revision == 1


async def test_save_only_deletes_explicit_ids_and_omitted_tasks_survive(db_session, monkeypatch):
    """缺席不删除也不取消，显式目标才执行取消。"""
    user, _, ep, rows = await make_episode(db_session)
    cancel = AsyncMock()
    monkeypatch.setattr('app.api.drama.episodes.cancel_fragment_video_tasks_for_fragments', cancel)
    out = await save_fragments(ep.id, request([item(rows[0])]), db_session, user)
    assert {f.id for f in out.fragments} == {f.id for f in rows}
    cancel.assert_not_called()
    out = await save_fragments(ep.id, request([], revision=1, deleted_ids=[rows[0].id]), db_session, user)
    assert [f.id for f in out.fragments] == [rows[1].id]
    assert cancel.await_count == 1
    assert cancel.await_args.args == (db_session, [rows[0].id])
    assert "prepared_settlements" in cancel.await_args.kwargs


@pytest.mark.parametrize('bad', ['unknown', 'foreign', 'duplicate', 'both', 'duplicate_delete', 'unknown_delete'])
async def test_invalid_ids_reject_entire_save(db_session, bad):
    """未知、跨集、重复及交叉标记不能留下部分写入。"""
    user, project, ep, rows = await make_episode(db_session)
    other = DramaEpisode(project_id=project.id, name='other')
    db_session.add(other)
    await db_session.flush()
    foreign = DramaEpisodeFragment(episode_id=other.id, content='foreign')
    db_session.add(foreign)
    await db_session.commit()
    values = [item(rows[0])]
    deletes = []
    if bad == 'unknown': values.append({'id': 999999999, 'content': 'bad'})
    if bad == 'foreign': values.append(item(foreign))
    if bad == 'duplicate': values.append(item(rows[0]))
    if bad == 'both': deletes = [rows[0].id]
    if bad == 'duplicate_delete': deletes = [rows[1].id, rows[1].id]
    if bad == 'unknown_delete': deletes = [999999999]
    with pytest.raises(HTTPException) as exc:
        await save_fragments(ep.id, request(values, deleted_ids=deletes), db_session, user)
    assert exc.value.status_code in {400, 409}
    await db_session.refresh(ep)
    await db_session.refresh(rows[0])
    assert getattr(ep, 'revision', 0) == 0
    assert rows[0].content == '原稿0'


async def test_active_generation_and_new_fragments_reject_runtime_injection(db_session):
    """无视频的在途任务也保留服务端状态，新镜不能伪造历史。"""
    user, _, ep, rows = await make_episode(db_session)
    rows[0].params = {'generation': {'status': 'running'}, 'generation_attempts': 2}
    out = await save_fragments(ep.id, request([item(rows[0], params={}, video='fake'), {'content': 'new', 'video': 'fake', 'cover': 'fake', 'params': {'video_versions': [1], 'generation': {'status': 'done'}, 'user_setting': 'new'}}]), db_session, user)
    saved = next(f for f in out.fragments if f.id == rows[0].id)
    assert saved.video == ''
    assert saved.params['generation']['status'] == 'running'
    new = next(f for f in out.fragments if f.id not in {r.id for r in rows})
    assert new.video == new.cover == ''
    assert new.params == {'user_setting': 'new'}


async def test_seed_replacement_invalidates_old_save(db_session):
    """后台规则重拆也递增版本，旧标签不能复活已删镜。"""
    from app.services.drama.seed import _replace_episode_fragments
    user, _, ep, rows = await make_episode(db_session)
    await _replace_episode_fragments(db_session, ep, 'body', [], drafts=[{'content': '新拆'}])
    await db_session.commit()
    with pytest.raises(HTTPException, match='内容已在别处更新，请刷新') as exc:
        await save_fragments(ep.id, request([item(rows[0])]), db_session, user)
    assert exc.value.status_code == 409


@pytest_asyncio.fixture
async def committed_episode(db_session):
    """独立连接并发只向隔离测试库提交，finally 删除本用例用户树。"""
    engine = db_session.bind.engine
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user, project, ep, rows = await make_episode(db)
        await db.commit()
        yield engine, user, project, ep, rows
    async with AsyncSession(engine) as cleanup:
        await cleanup.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
        await cleanup.execute(delete(User).where(User.id == user.id))
        await cleanup.commit()


async def test_two_connections_same_revision_only_one_can_save(committed_episode):
    """真实 PG 两个独立事务竞争同一 revision 只有一方成功。"""
    engine, user, _, ep, rows = committed_episode
    ready = asyncio.Event()
    async def attempt(content):
        """屏障后同时发送相同快照。"""
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await ready.wait()
            try:
                return await save_fragments(ep.id, request([item(rows[0], content=content)]), db, user)
            except HTTPException as exc:
                await db.rollback()
                return exc
    tasks = [asyncio.create_task(attempt(c)) for c in ['A', 'B']]
    ready.set()
    results = await asyncio.gather(*tasks)
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    assert sum(not isinstance(r, HTTPException) for r in results) == 1


async def test_download_then_apply_refreshes_old_fragment_before_merging(committed_episode, monkeypatch):
    """模拟 poller 先读镜、下载阻塞、用户保存、旧对象收尾的真实边界。"""
    from app.services.drama.generation import apply_fragment_video_assets
    engine, user, project, ep, rows = committed_episode
    monkeypatch.setattr('app.services.drama.generation._snapshot_version_media_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda url: None)
    monkeypatch.setattr('app.services.drama.generation.record_seedance_video_usage', AsyncMock())
    async with AsyncSession(engine) as setup:
        previous = await setup.get(DramaEpisodeFragment, rows[0].id)
        previous.video = 'https://old/video'
        previous.cover = 'https://old/cover'
        previous.params = {'user_setting': 'old', 'video_versions': [{'id': 'earlier', 'video': 'https://earlier/video'}], 'lastFrameUrl': 'https://old/tail'}
        await setup.commit()
    downloading, downloaded = asyncio.Event(), asyncio.Event()
    async def poller():
        """下载前缓存对象，在屏障后运行实际生成结果写入。"""
        async with AsyncSession(engine, expire_on_commit=False) as db:
            stale = await db.get(DramaEpisodeFragment, rows[0].id)
            await db.commit()
            downloading.set()
            await downloaded.wait()
            await apply_fragment_video_assets(db, user, project, stale, local_video='https://new/video', local_last_frame='https://new/tail')
    task = asyncio.create_task(poller())
    await downloading.wait()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'after-download', 'new_setting': True})]), db, user)
    downloaded.set()
    await task
    async with AsyncSession(engine) as db:
        row = await db.get(DramaEpisodeFragment, rows[0].id)
        assert row.params['user_setting'] == 'after-download'
        assert row.params['new_setting'] is True
        assert row.content != '原稿0'
        assert row.video == 'https://new/video'
        assert row.params['lastFrameUrl'] == 'https://new/tail'
        assert row.params['generation']['status'] == 'done'
        assert row.cover == 'https://new/tail'
        assert row.params['video_versions'][0]['video'] == 'https://old/video'
        assert row.params['video_versions'][0]['lastFrameUrl'] == 'https://old/tail'
        assert row.params['video_versions'][1]['id'] == 'earlier'



async def test_llm_plan_cannot_replace_edits_made_while_waiting(committed_episode, monkeypatch):
    """LLM 等待期间保存的新编辑使草稿过期，但已发生的调用仍记用量。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models_drama import DramaScript
    from app.services.drama.jobs import run_episode_fragment_plan_job
    engine, user, project, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(DramaScript(project_id=project.id, name='script', episode_content=[{'episodeNumber': 1, 'body': '剧本正文'}]))
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    async def llm(**kwargs):
        """只阻塞外部 LLM，数据库与实际 worker 写入照常执行。"""
        entered.set()
        await release.wait()
        return [{'content': '过期LLM草稿'}]
    usage = AsyncMock(wraps=prepare_usage_line)
    monkeypatch.setattr('app.services.drama.jobs.AsyncSessionLocal', factory)
    monkeypatch.setattr('app.services.drama.fragment_plan.plan_fragments_with_llm', llm)
    monkeypatch.setattr('app.services.drama.character_intro_llm.prepare_character_intro_overrides', AsyncMock(return_value={}))
    monkeypatch.setattr('app.services.drama.jobs.prepare_usage_line', usage)
    task = asyncio.create_task(run_episode_fragment_plan_job(ep.id, expected_revision=0, force=True))
    await asyncio.wait_for(entered.wait(), 5)
    async with factory() as db:
        await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'new'})]), db, user)
    release.set()
    result = await task
    assert result['ok'] is False
    assert result['error'] == '内容已在别处更新，请刷新'
    usage.assert_awaited_once()
    async with factory() as db:
        usage_events = list((await db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id, UsageEvent.billing_key == 'llm_chat'))).all())
        assert len(usage_events) == 1
        assert usage_events[0].cost_fen > 0
        row = await db.get(DramaEpisodeFragment, rows[0].id)
        assert row.params['user_setting'] == 'new'
        assert len(list((await db.scalars(select(DramaEpisodeFragment).where(DramaEpisodeFragment.episode_id == ep.id))).all())) == 2


async def test_two_force_plans_returning_in_reverse_do_not_replace_newer_result(committed_episode, monkeypatch):
    """先启动的旧规划最后返回时不能覆盖已落库的新规划。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models_drama import DramaScript
    from app.services.drama.jobs import run_episode_fragment_plan_job
    engine, _, project, ep, _ = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(DramaScript(project_id=project.id, name='script', episode_content=[{'episodeNumber': 1, 'body': '正文'}]))
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def llm(**kwargs):
        """第一个调用阻塞，第二个先返回。"""
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            await release.wait()
            return [{'content': '旧规划'}]
        return [{'content': '新规划'}]
    monkeypatch.setattr('app.services.drama.jobs.AsyncSessionLocal', factory)
    monkeypatch.setattr('app.services.drama.fragment_plan.plan_fragments_with_llm', llm)
    monkeypatch.setattr('app.services.drama.character_intro_llm.prepare_character_intro_overrides', AsyncMock(return_value={}))
    monkeypatch.setattr('app.services.drama.jobs.prepare_usage_line', AsyncMock(wraps=prepare_usage_line))
    older = asyncio.create_task(run_episode_fragment_plan_job(ep.id, expected_revision=0, force=True))
    await asyncio.wait_for(entered.wait(), 5)
    newer = await run_episode_fragment_plan_job(ep.id, expected_revision=0, force=True)
    release.set()
    old_result = await older
    assert newer['ok'] is True
    assert old_result == {'ok': False, 'error': '内容已在别处更新，请刷新'}
    async with factory() as db:
        contents = list((await db.scalars(select(DramaEpisodeFragment.content).where(DramaEpisodeFragment.episode_id == ep.id))).all())
        assert contents == ['新规划']


@pytest.mark.parametrize('stage', ['submit', 'download'])
async def test_real_video_worker_wait_boundary_keeps_concurrent_user_params(committed_episode, monkeypatch, stage):
    """真实提交器/轮询器在上游等待中插入用户保存，再验证实际收尾。"""
    from types import SimpleNamespace
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models_tasks import TaskRun
    from app.services.drama.jobs import poll_fragment_video_task, submit_fragment_video_task
    engine, user, project, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        task = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id,
                       status='running' if stage == 'submit' else 'awaiting_poll',
                       episode_id=ep.id, fragment_id=rows[0].id, drama_project_id=project.id,
                       provider_task_id='upstream' if stage == 'download' else None,
                       payload={'nio_phase': 'submit', 'prepared': {'submit_mode': 'test'}})
        db.add(task)
        await db.commit()
        task_id = task.id
    entered, release = asyncio.Event(), asyncio.Event()
    async def external(*args, **kwargs):
        """替代外部网络，只在可确定的屏障处让用户保存通过。"""
        entered.set()
        await release.wait()
        return 'upstream' if stage == 'submit' else ('https://new/video', 'https://new/tail')
    monkeypatch.setattr('app.services.drama.jobs.AsyncSessionLocal', factory)
    monkeypatch.setattr('app.services.drama.jobs.submit_prepared_fragment_video', external)
    monkeypatch.setattr('app.services.ark.get_ark', lambda: SimpleNamespace(fetch_task_once=AsyncMock(return_value=SimpleNamespace(status='succeeded')), save_video_assets_from_result=external))
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda url: None)
    monkeypatch.setattr('app.services.drama.generation.record_seedance_video_usage', AsyncMock())
    # 后续调度、结算不属于此回归；保留真实数据库 claim 和媒体写入。
    monkeypatch.setattr('app.services.drama.jobs._recover_complete_fragment_video', AsyncMock())
    worker = asyncio.create_task(submit_fragment_video_task(task) if stage == 'submit' else poll_fragment_video_task(task_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with factory() as db:
            await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'during-network', 'new_setting': True})]), db, user)
        release.set()
        await asyncio.wait_for(worker, 5)
        async with factory() as db:
            row = await db.get(DramaEpisodeFragment, rows[0].id)
            assert row.params['user_setting'] == 'during-network'
            assert row.params['new_setting'] is True
            assert row.params['generation']['status'] == ('running' if stage == 'submit' else 'done')
            if stage == 'download':
                assert row.video == 'https://new/video'
                assert row.params['lastFrameUrl'] == 'https://new/tail'
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        async with factory() as db:
            await db.execute(delete(TaskRun).where(TaskRun.id == task_id))
            await db.commit()


async def test_tail_extraction_wait_keeps_new_user_params(committed_episode, monkeypatch, tmp_path):
    """准备阶段给上一镜补尾帧时，下载前缓存的 JSON 不能覆盖用户编辑。"""
    from app.services.drama.generation import ensure_fragment_last_frame_url
    engine, user, project, ep, rows = committed_episode
    async with AsyncSession(engine) as db:
        row = await db.get(DramaEpisodeFragment, rows[0].id)
        row.video = 'https://existing/video'
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    async def download(url, path):
        """只替换下载和抽帧，仍使用真实 ORM 回写。"""
        entered.set()
        await release.wait()
        path.write_bytes(b'fake-video')
    monkeypatch.setattr('app.services.storage.local_path_from_url', lambda url: None)
    monkeypatch.setattr('app.services.storage.project_dir', lambda project_id: tmp_path)
    monkeypatch.setattr('app.services.storage.download_to', download)
    monkeypatch.setattr('app.services.storage.republish_url', lambda url, **kwargs: url)
    monkeypatch.setattr('app.services.storage.rel_static_url', lambda path: 'https://tail')
    monkeypatch.setattr('app.services.ffmpeg_compose.extract_video_last_frame', lambda *args: True)
    async def prepare():
        """复现 prepare 内对上一镜的补尾帧路径。"""
        async with AsyncSession(engine, expire_on_commit=False) as db:
            stale = await db.get(DramaEpisodeFragment, rows[0].id)
            await ensure_fragment_last_frame_url(project, stale)
            await db.commit()
    worker = asyncio.create_task(prepare())
    await asyncio.wait_for(entered.wait(), 5)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'during-tail'})]), db, user)
    release.set()
    await asyncio.wait_for(worker, 5)
    async with AsyncSession(engine) as db:
        row = await db.get(DramaEpisodeFragment, rows[0].id)
        assert row.params['user_setting'] == 'during-tail'
        assert row.params['lastFrameUrl'] == 'https://tail'


async def test_task_failure_from_cached_fragment_does_not_erase_saved_params(committed_episode, monkeypatch):
    """公共任务失败收敛即使缓存旧分镜，也仅写运行态字段。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models_tasks import TaskRun
    from app.services.tasks.executor import _fail_task
    from app.services.tasks.service import get_task_for_runtime
    engine, user, _, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        task = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id, status='running', episode_id=ep.id, fragment_id=rows[0].id)
        db.add(task)
        await db.commit()
        task_id = task.id
    monkeypatch.setattr('app.services.tasks.executor.settle_task', AsyncMock())
    try:
        async with factory() as fail_db:
            task = await get_task_for_runtime(fail_db, task_id)
            cached = await fail_db.get(DramaEpisodeFragment, rows[0].id)
            async with factory() as save_db:
                await save_fragments(ep.id, request([item(rows[0], params={'user_setting': 'new'})]), save_db, user)
            await _fail_task(fail_db, task, RuntimeError('上游失败'))
            await fail_db.refresh(cached)
            assert cached.params['user_setting'] == 'new'
            assert cached.params['generation']['status'] == 'failed'
    finally:
        async with factory() as db:
            await db.execute(delete(TaskRun).where(TaskRun.id == task_id))
            await db.commit()


async def test_explicit_delete_and_locked_task_failure_do_not_deadlock(committed_episode, monkeypatch):
    """删除需取消任务时先等任务锁，再锁分镜，允许已认领的失败回写完成。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models_tasks import TaskRun
    from app.services.tasks.executor import _fail_task
    from app.services.tasks.service import get_task_for_runtime
    from app.api.drama import episodes as endpoints
    engine, user, _, ep, rows = committed_episode
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        task = TaskRun(domain='drama', task_type='fragment_video', requested_by=user.id, status='running', episode_id=ep.id, fragment_id=rows[0].id)
        db.add(task)
        await db.commit()
        task_id = task.id
    cancelling = asyncio.Event()
    original_cancel = endpoints.cancel_fragment_video_tasks_for_fragments
    async def cancel(db, ids, **kwargs):
        """保留真实取消，只在其将获取任务行锁时发出屏障信号。"""
        cancelling.set()
        return await original_cancel(db, ids, **kwargs)
    monkeypatch.setattr(endpoints, 'cancel_fragment_video_tasks_for_fragments', cancel)
    monkeypatch.setattr('app.services.tasks.executor.settle_task', AsyncMock())
    async def save_deleted():
        """在独立事务中删除正在失败收敛的分镜。"""
        async with factory() as db:
            return await save_fragments(ep.id, request([], deleted_ids=[rows[0].id]), db, user)
    saving = None
    try:
        async with factory() as fail_db:
            await fail_db.scalar(select(TaskRun).where(TaskRun.id == task_id).with_for_update())
            locked = await get_task_for_runtime(fail_db, task_id)
            saving = asyncio.create_task(save_deleted())
            await asyncio.wait_for(cancelling.wait(), 5)
            await asyncio.wait_for(_fail_task(fail_db, locked, RuntimeError('失败')), 5)
        saved = await asyncio.wait_for(saving, 5)
        assert [f.id for f in saved.fragments] == [rows[1].id]
    finally:
        if saving is not None and not saving.done():
            saving.cancel()
            await asyncio.gather(saving, return_exceptions=True)
        async with factory() as db:
            await db.execute(delete(TaskRun).where(TaskRun.id == task_id))
            await db.commit()


def test_missing_or_negative_revision_is_rejected_before_save():
    """旧客户端缺少版本时不能默认使用服务端当前版本绕过冲突。"""
    from pydantic import ValidationError
    for body in [{'fragments': []}, {'fragments': [], 'revision': -1}]:
        with pytest.raises(ValidationError):
            DramaSaveFragmentsRequest.model_validate(body)
