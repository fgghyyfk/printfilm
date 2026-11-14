"""真实并发发布：两个独立 HTTP 请求/数据库事务，验证 PG 锁和提交可见性。"""
import asyncio
import re
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
import httpx
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_db
from app.deps import get_current_user
from app.models import Project, Shot, Template, User
from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.models_teams import Team, TeamMember
from app.marketplace import drama_media, drama_publish, storage
from app.marketplace.models import DramaPublishBatch, Handoff, MarketBase, Series, SeriesEpisode
from conftest import _ensure_test_database, _resolve_test_database_url


@pytest_asyncio.fixture
async def concurrent_publish_ctx(monkeypatch):
    """独立测试库内提交随机 schema，finally 仅删除本用例拥有的 schema。"""
    url, name = _resolve_test_database_url(get_settings().database_url)
    await _ensure_test_database(url, name)
    base_engine = create_async_engine(url, pool_size=5)
    schema = f"drama_concurrency_test_{uuid.uuid4().hex}"
    assert re.fullmatch(r"drama_concurrency_test_[0-9a-f]{32}", schema)
    engine = base_engine.execution_options(schema_translate_map={None: schema})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(engine=engine, sessions=sessions, request_pids=[])
    try:
        async with base_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            tables = [item.__table__ for item in (
                User, Team, TeamMember, Template, Project, Shot, DramaProject, DramaScript,
                DramaAsset, DramaEpisode, DramaEpisodeFragment, TaskRun,
            )]
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            await connection.run_sync(MarketBase.metadata.create_all)
        async with sessions() as db:
            owner = User(email="concurrency-owner@test.invalid", hashed_password="unused")
            db.add(owner)
            await db.flush()
            project = DramaProject(user_id=owner.id, title="并发整集发布", description="独立来源快照")
            db.add(project)
            await db.flush()
            episode = DramaEpisode(project_id=project.id, name="第1集", params={"episodeNumber": 1})
            db.add(episode)
            await db.flush()
            for order in range(3):
                db.add(DramaEpisodeFragment(episode_id=episode.id, sort_order=order,
                    video=f"/static/test/concurrent-shot-{order}.mp4"))
            await db.commit()
            state.owner_id, state.project_id, state.episode_id = owner.id, project.id, episode.id

        async def db_override():
            # No shared outer transaction or savepoint: each HTTP request must
            # really commit, release PG locks and expose rows to the other.
            async with sessions() as db:
                state.request_pids.append(await db.scalar(text("SELECT pg_backend_pid()")))
                try:
                    yield db
                except BaseException:
                    await db.rollback()
                    raise

        async def actor_override():
            return SimpleNamespace(id=state.owner_id, role="user")

        monkeypatch.setattr(storage, "bucket", lambda: object())
        monkeypatch.setattr(storage, "configured", lambda: True)
        monkeypatch.setattr(drama_media, "source_stamp", lambda locator: {
            "kind": "local", "locator": locator, "size_bytes": 100,
            "file_id": "stable-source", "mtime_ns": 1, "fingerprint": "test-source",
        })
        async with sessions() as db:
            snapshot = await drama_publish.episode_snapshot(db, state.owner_id, state.project_id, state.episode_id)
            state.source_version = snapshot["source_version"]
        app = FastAPI()
        app.include_router(drama_publish.router, prefix="/api/marketplace")
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = actor_override
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://first") as first,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://second") as second,
        ):
            state.clients = (first, second)
            yield state
    finally:
        # The generated identifier is checked again at the destructive boundary.
        # No shared/public schema or pre-existing test tables are touched.
        assert re.fullmatch(r"drama_concurrency_test_[0-9a-f]{32}", schema)
        async with base_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await base_engine.dispose()


def payload(ctx, key, *, series_id=None, series_version=None):
    return {"episodes": [{"episode_id": ctx.episode_id, "source_version": ctx.source_version}],
            "idempotency_key": key, "series_id": series_id, "series_version": series_version}


async def wait_for_two_database_waiters(ctx):
    """不依赖调度运气：确认两个不同 PG 连接实际争用锁后才放行。"""
    async with asyncio.timeout(10):
        async with ctx.engine.connect() as observer:
            while True:
                if len(ctx.request_pids) == 2:
                    first, second = ctx.request_pids
                    assert first != second, "必须使用两个独立数据库连接"
                    waiting = set((await observer.execute(text(
                        "SELECT DISTINCT pid FROM pg_locks WHERE pid IN (:first, :second) AND NOT granted"
                    ), {"first": first, "second": second})).scalars().all())
                    if waiting == {first, second}:
                        return
                await asyncio.sleep(0.01)


async def race_requests(ctx, bodies, *, guard_key=None, guard_series_id=None):
    """第三个事务先持锁，让两个请求确定重叠；释放后使用真实提交顺序。"""
    tasks = []
    async with ctx.sessions() as guard:
        try:
            if guard_key is not None:
                lock_id = int(drama_publish.digest([ctx.owner_id, guard_key])[:16], 16)
                if lock_id >= 2**63:
                    lock_id -= 2**64
                await guard.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
            else:
                await guard.execute(select(Series).where(Series.id == guard_series_id).with_for_update())
            path = f"/api/marketplace/printfilm/dramas/{ctx.project_id}/publish"
            tasks = [asyncio.create_task(client.post(path, json=body)) for client, body in zip(ctx.clients, bodies)]
            await wait_for_two_database_waiters(ctx)
            assert not any(task.done() for task in tasks)
            await guard.commit()
            return await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        finally:
            await guard.rollback()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


async def test_concurrent_same_key_new_series_creates_one_batch_episode_and_handoff(concurrent_publish_ctx):
    ctx = concurrent_publish_ctx
    key = uuid.uuid4().hex
    body = payload(ctx, key)
    responses = await race_requests(ctx, (body, body), guard_key=key)
    assert [response.status_code for response in responses] == [200, 200], [response.text for response in responses]
    assert responses[0].json() == responses[1].json()
    async with ctx.sessions() as db:
        for model in (Series, DramaPublishBatch, SeriesEpisode, Handoff):
            assert await db.scalar(select(func.count()).select_from(model)) == 1
        target = (await db.execute(select(SeriesEpisode))).scalar_one()
        handoff = (await db.execute(select(Handoff))).scalar_one()
        batch = (await db.execute(select(DramaPublishBatch))).scalar_one()
        assert batch.episode_ids == [target.id] and batch.handoff_ids == [handoff.id]
        assert handoff.target_id == target.id and handoff.status == "pending"
        assert [clip["sort_order"] for clip in handoff.source_snapshot["clips"]] == [0, 1, 2]


async def test_concurrent_different_keys_same_target_source_version_never_duplicates_episode(concurrent_publish_ctx):
    ctx = concurrent_publish_ctx
    async with ctx.sessions() as db:
        target = Series(owner_id=ctx.owner_id, title="已有系列", description="保留已有简介")
        db.add(target)
        await db.commit()
        series_id, version = target.id, target.version
    bodies = [payload(ctx, uuid.uuid4().hex, series_id=series_id, series_version=version) for _ in range(2)]
    responses = await race_requests(ctx, bodies, guard_series_id=series_id)
    statuses = [response.status_code for response in responses]
    assert 200 in statuses and all(status in (200, 409) for status in statuses), [response.text for response in responses]
    successes = [response.json() for response in responses if response.status_code == 200]
    assert len({item["items"][0]["id"] for item in successes}) == 1
    assert len({item["handoffs"][0]["id"] for item in successes}) == 1
    async with ctx.sessions() as db:
        for model in (Series, SeriesEpisode, Handoff):
            assert await db.scalar(select(func.count()).select_from(model)) == 1
        assert await db.scalar(select(func.count()).select_from(DramaPublishBatch)) == len(successes)
        target = await db.get(Series, series_id)
        assert target.title == "已有系列" and target.description == "保留已有简介"
        assert target.version == version + 1
