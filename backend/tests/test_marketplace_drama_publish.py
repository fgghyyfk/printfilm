"""漫剧到市场的整集发布：隔离测试库/schema，文件存储仅使用测试替身。"""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from datetime import timedelta
import uuid

from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from app.database import Base, get_db
from app.deps import get_current_user
from app.models import Project, Shot, Template, User
from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject, DramaScript
from app.models_tasks import TaskRun
from app.marketplace import drama_media, drama_publish, handoff, storage
from app.marketplace.authorization import VERSION
from app.marketplace.models import Asset, Authorization, DramaPublishBatch, Handoff, MarketBase, Publication, Rights, Series, SeriesEpisode
from app.marketplace.service import now
from conftest import _ensure_test_database, _resolve_test_database_url


@pytest_asyncio.fixture
async def drama_publish_ctx(monkeypatch):
    url, name = _resolve_test_database_url(get_settings().database_url)
    await _ensure_test_database(url, name)
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        schema = f"drama_publish_test_{uuid.uuid4().hex}"
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection = await connection.execution_options(schema_translate_map={None: schema})
        tables = [item.__table__ for item in (User, Template, Project, Shot, DramaProject, DramaScript, DramaAsset, DramaEpisode, DramaEpisodeFragment, TaskRun)]
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await connection.run_sync(MarketBase.metadata.create_all)
        db = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        owner = User(email="drama-owner@test.invalid", hashed_password="unused")
        other = User(email="drama-other@test.invalid", hashed_password="unused")
        db.add_all([owner, other])
        await db.commit()
        ctx = SimpleNamespace(db=db, owner=owner, other=other, user=owner, source_revision=1)

        async def db_override():
            try:
                yield db
            except Exception:
                await db.rollback()
                await db.refresh(owner)
                await db.refresh(other)
                raise

        async def actor_override():
            return ctx.user

        def source_stamp(locator):
            return {"kind": "local", "locator": locator, "size_bytes": 100, "file_id": "source-file", "mtime_ns": ctx.source_revision, "fingerprint": f"revision-{ctx.source_revision}"}

        app = FastAPI()
        app.include_router(drama_publish.router, prefix="/api/marketplace")
        app.include_router(handoff.router, prefix="/api/marketplace")
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = actor_override
        monkeypatch.setattr(storage, "bucket", lambda: object())
        monkeypatch.setattr(storage, "configured", lambda: True)
        monkeypatch.setattr(drama_media, "source_stamp", source_stamp)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                ctx.client = client
                yield ctx
        finally:
            await db.close()
            await outer.rollback()
    await engine.dispose()


async def source(ctx, *, owner=None, episodes=1, fragments=3):
    project = DramaProject(user_id=(owner or ctx.owner).id, title="完整漫剧", description="导入后的独立介绍")
    ctx.db.add(project)
    await ctx.db.flush()
    items, clips = [], []
    for number in range(1, episodes + 1):
        episode = DramaEpisode(project_id=project.id, name=f"原第{number}集", params={"episodeNumber": number})
        ctx.db.add(episode)
        await ctx.db.flush()
        for index in range(fragments):
            # 故意逆序创建，用排序断言防止只导入最后一镜。
            order = fragments - index - 1
            clip = DramaEpisodeFragment(episode_id=episode.id, sort_order=order, video=f"/static/test/episode-{number}-shot-{order}.mp4", cover=f"/static/test/cover-{number}.png")
            ctx.db.add(clip)
            clips.append(clip)
        items.append(episode)
    await ctx.db.commit()
    return project, items, clips


async def publish_payload(ctx, project, episodes, *, key=None, series=None, update=False):
    snapshot = [await drama_publish.episode_snapshot(ctx.db, ctx.owner.id, project.id, episode.id) for episode in episodes]
    return {"episodes": [{"episode_id": item["drama_episode_id"], "source_version": item["source_version"], "update": update} for item in snapshot],
            "series_id": series.id if series else None, "series_version": series.version if series else None, "idempotency_key": key or uuid.uuid4().hex}


async def post_publish(ctx, project, payload):
    return await ctx.client.post(f"/api/marketplace/printfilm/dramas/{project.id}/publish", json=payload)


async def published(ctx):
    project, episodes, clips = await source(ctx)
    response = await post_publish(ctx, project, await publish_payload(ctx, project, episodes))
    assert response.status_code == 200, response.text
    body = response.json()
    task = await ctx.db.get(Handoff, body["handoffs"][0]["id"])
    target = await ctx.db.get(SeriesEpisode, body["items"][0]["id"])
    series = await ctx.db.get(Series, body["series_id"])
    return SimpleNamespace(project=project, episodes=episodes, clips=clips, task=task, target=target, series=series)


async def test_context_shows_complete_counts_and_all_owned_target_series(drama_publish_ctx):
    ctx = drama_publish_ctx
    project, episodes, clips = await source(ctx, episodes=2)
    clips[0].video = ""
    own = Series(owner_id=ctx.owner.id, title="我的其他系列")
    foreign = Series(owner_id=ctx.other.id, title="其他人的系列")
    ctx.db.add_all([own, foreign])
    await ctx.db.commit()
    response = await ctx.client.get(f"/api/marketplace/printfilm/dramas/{project.id}/publish-context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["description"] == project.description
    assert body["episodes"][0]["fragment_count"] == 3 and body["episodes"][0]["completed_count"] == 2
    assert body["episodes"][0]["ready"] is False and "SOURCE_NOT_READY" in body["episodes"][0]["reason"]
    assert body["episodes"][1]["ready"] is True
    assert [item["id"] for item in body["series"]] == [own.id]
    assert body["storage_configured"] is True and body["authorization_ready"] is False
    assert "/static/" not in response.text


@pytest.mark.parametrize("case", ["foreign_project", "foreign_episode", "foreign_target"])
async def test_publish_rejects_cross_account_sources_and_targets(drama_publish_ctx, case):
    ctx = drama_publish_ctx
    project, episodes, _ = await source(ctx)
    payload = await publish_payload(ctx, project, episodes)
    if case == "foreign_project":
        ctx.user = ctx.other
    elif case == "foreign_episode":
        other_project, other_episodes, _ = await source(ctx, owner=ctx.other)
        payload["episodes"][0]["episode_id"] = other_episodes[0].id
    else:
        target = Series(owner_id=ctx.other.id, title="其他系列")
        ctx.db.add(target)
        await ctx.db.commit()
        payload["series_id"] = target.id
        payload["series_version"] = target.version
    response = await post_publish(ctx, project, payload)
    assert response.status_code == 404, response.text
    assert await ctx.db.scalar(select(func.count()).select_from(Handoff)) == 0
    assert await ctx.db.scalar(select(func.count()).select_from(DramaPublishBatch)) == 0


@pytest.mark.parametrize("case", ["missing_shot", "task_video", "task_plan", "episode_plan", "shot_video"])
async def test_incomplete_or_generating_episode_creates_no_market_draft(drama_publish_ctx, case):
    ctx = drama_publish_ctx
    project, episodes, clips = await source(ctx)
    payload = await publish_payload(ctx, project, episodes)
    if case == "missing_shot":
        clips[0].video = ""
    elif case.startswith("task_"):
        ctx.db.add(TaskRun(domain="drama", task_type="fragment_video" if case == "task_video" else "fragment_plan", status="running", requested_by=ctx.owner.id,
                           episode_id=episodes[0].id, drama_project_id=project.id, fragment_id=clips[0].id))
    elif case == "episode_plan":
        episodes[0].params = {"episodeNumber": 1, "fragment_plan_status": "running"}
    else:
        clips[0].params = {"video_gen": {"status": "running"}}
    await ctx.db.commit()
    response = await post_publish(ctx, project, payload)
    assert response.status_code == 409 and "MARKETPLACE_SOURCE_NOT_READY" in response.text
    assert await ctx.db.scalar(select(func.count()).select_from(Series)) == 0
    assert await ctx.db.scalar(select(func.count()).select_from(Handoff)) == 0


async def test_storage_unavailable_never_creates_empty_series(drama_publish_ctx, monkeypatch):
    ctx = drama_publish_ctx
    project, episodes, _ = await source(ctx)
    payload = await publish_payload(ctx, project, episodes)

    def unavailable():
        raise HTTPException(503, {"code": "MARKETPLACE_STORAGE_UNAVAILABLE", "message": "文件服务暂未配置"})

    monkeypatch.setattr(storage, "bucket", unavailable)
    response = await post_publish(ctx, project, payload)
    assert response.status_code == 503 and "MARKETPLACE_STORAGE_UNAVAILABLE" in response.text
    assert await ctx.db.scalar(select(func.count()).select_from(Series)) == 0


async def test_atomic_publish_is_idempotent_orders_all_clips_and_keeps_upstream(drama_publish_ctx):
    ctx = drama_publish_ctx
    project, episodes, clips = await source(ctx, episodes=2)
    payload = await publish_payload(ctx, project, list(reversed(episodes)))
    response = await post_publish(ctx, project, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    again = await post_publish(ctx, project, payload)
    assert again.status_code == 200 and again.json() == result
    assert await ctx.db.scalar(select(func.count()).select_from(Series)) == 1
    assert await ctx.db.scalar(select(func.count()).select_from(SeriesEpisode)) == 2
    assert await ctx.db.scalar(select(func.count()).select_from(Handoff)) == 2
    target = await ctx.db.get(Series, result["series_id"])
    assert target.title == project.title and target.description == project.description
    for index, item in enumerate(result["items"]):
        assert item["episode_no"] == index + 1 and item["title"] == episodes[index].name
        task = await ctx.db.get(Handoff, result["handoffs"][index]["id"])
        assert [clip["sort_order"] for clip in task.source_snapshot["clips"]] == [0, 1, 2]
        assert len(task.source_snapshot["clips"]) == 3
    assert all(clip.video.startswith("/static/test/") for clip in clips)
    payload["title"] = "另一次不同发布"
    conflict = await post_publish(ctx, project, payload)
    assert conflict.status_code == 409


async def test_same_source_version_in_same_target_reuses_episode_across_batches(drama_publish_ctx):
    ctx = drama_publish_ctx
    existing = await published(ctx)
    response = await post_publish(ctx, existing.project, await publish_payload(ctx, existing.project, existing.episodes, series=existing.series))
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == existing.target.id
    assert response.json()["handoffs"][0]["id"] == existing.task.id
    assert await ctx.db.scalar(select(func.count()).select_from(SeriesEpisode)) == 1
    assert await ctx.db.scalar(select(func.count()).select_from(Handoff)) == 1


async def test_changed_source_requires_confirmation_and_preserves_old_publication_asset(drama_publish_ctx):
    ctx = drama_publish_ctx
    existing = await published(ctx)
    old_asset = Asset(owner_id=ctx.owner.id, kind="video", filename="old.mp4", content_type="video/mp4", size_bytes=100, object_key="marketplace/old.mp4", status="ready")
    ctx.db.add(old_asset)
    await ctx.db.flush()
    existing.target.asset_id, existing.target.audit_status, existing.target.listed = old_asset.id, "approved", True
    existing.target.source_snapshot = {**existing.target.source_snapshot, "pending_handoff_id": None, "last_handoff_id": existing.task.id}
    existing.task.status, existing.task.asset_id = "ready", old_asset.id
    existing.series.audit_status, existing.series.listed = "approved", True
    publication = Publication(content_type="series_episode", content_id=existing.target.id, owner_id=ctx.owner.id, version=existing.target.version,
                              asset_id=old_asset.id, sale_type="copyright_contact", audit_status="approved", listed=True)
    ctx.db.add(publication)
    await ctx.db.commit()
    ctx.source_revision = 2
    payload = await publish_payload(ctx, existing.project, existing.episodes, series=existing.series)
    response = await post_publish(ctx, existing.project, payload)
    assert response.status_code == 409 and "SOURCE_CHANGED" in response.text
    # Failed request rolls back the savepoint; fetch state again before a new request.
    await ctx.db.refresh(existing.series)
    await ctx.db.refresh(existing.target)
    await ctx.db.refresh(publication)
    await ctx.db.refresh(existing.project)
    await ctx.db.refresh(old_asset)
    for episode in existing.episodes:
        await ctx.db.refresh(episode)
    payload = await publish_payload(ctx, existing.project, existing.episodes, series=existing.series, update=True)
    response = await post_publish(ctx, existing.project, payload)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == existing.target.id
    assert existing.target.asset_id == old_asset.id and publication.asset_id == old_asset.id
    assert existing.series.listed is False and existing.series.audit_status == "draft"
    assert existing.target.listed is False and publication.listed is False
    assert await ctx.db.scalar(select(func.count()).select_from(Handoff)) == 2


@pytest.mark.parametrize("case", ["version", "asset", "new_task", "blocked", "deleted_series"])
async def test_fenced_bind_never_overwrites_modified_deleted_or_superseded_target(drama_publish_ctx, case):
    ctx = drama_publish_ctx
    existing = await published(ctx)
    candidate = Asset(owner_id=ctx.owner.id, kind="video", filename="new.mp4", content_type="video/mp4", size_bytes=100, object_key=f"marketplace/{uuid.uuid4().hex}.mp4", status="ready")
    ctx.db.add(candidate)
    await ctx.db.flush()
    if case == "version":
        existing.target.version += 1
    elif case == "asset":
        existing.target.asset_id = candidate.id
    elif case == "new_task":
        existing.target.source_snapshot = {**existing.target.source_snapshot, "pending_handoff_id": existing.task.id + 1}
    elif case == "blocked":
        existing.target.blocked = True
    else:
        existing.series.deleted_at = now()
    before = existing.target.asset_id
    await ctx.db.flush()
    with pytest.raises(HTTPException, match="MARKETPLACE_TARGET_CHANGED"):
        await handoff._bind(ctx.db, existing.task, candidate.id, None)
    assert existing.target.asset_id == before


async def test_successful_bind_auto_attaches_authorization_without_approving_rights(drama_publish_ctx):
    ctx = drama_publish_ctx
    existing = await published(ctx)
    ctx.db.add(Authorization(owner_id=ctx.owner.id, template_version=VERSION, signer_name="测试用户", body="terms", body_hash="0"*64,
                             letter_key="marketplace/letter.png", signature_key="marketplace/signature.png", client_ip="127.0.0.1", user_agent="test"))
    candidate = Asset(owner_id=ctx.owner.id, kind="video", filename="new.mp4", content_type="video/mp4", size_bytes=100, object_key="marketplace/new.mp4", status="ready")
    ctx.db.add(candidate)
    await ctx.db.flush()
    existing.task.asset_id = candidate.id
    await handoff._bind(ctx.db, existing.task, candidate.id, None)
    await ctx.db.flush()
    proof = (await ctx.db.execute(select(Rights).where(Rights.asset_id == candidate.id))).scalar_one()
    assert existing.target.asset_id == candidate.id and existing.task.status == "ready"
    assert proof.details["source"] == "printfilm" and int(proof.details["authorization_id"]) > 0
    assert proof.basic_status != "approved" and proof.full_status != "approved"


async def test_source_file_change_is_detected_before_worker_binding(drama_publish_ctx):
    ctx = drama_publish_ctx
    existing = await published(ctx)
    ctx.source_revision += 1
    with pytest.raises(HTTPException, match="MARKETPLACE_SOURCE_CHANGED"):
        await handoff._validate_drama_source(ctx.db, existing.task)
    assert existing.target.asset_id is None


@pytest.mark.parametrize("case", ["success", "source_changed", "target_changed", "lease_stolen", "resume_expired_lease"])
async def test_worker_recovery_and_final_binding_guards(drama_publish_ctx, monkeypatch, case):
    """实际执行领取/阶段/终态；仅替换远端存储与媒体制作，不伪造任务状态。"""
    ctx = drama_publish_ctx
    existing = await published(ctx)
    discarded, produced, phases = [], [], []
    original_phase = handoff._phase

    @asynccontextmanager
    async def sessions():
        # 每个 worker 阶段顺序使用同一测试连接，保持随机 schema 和外层回滚。
        yield ctx.db

    async def phase(handoff_id, token, value):
        phases.append(value)
        return await original_phase(handoff_id, token, value)

    async def prepare_video(snapshot, destination, on_phase):
        assert [item["sort_order"] for item in snapshot["clips"]] == [0, 1, 2]
        produced.append(destination)
        for value in ("copying", "composing", "validating"):
            await on_phase(value)
        if case == "source_changed":
            ctx.source_revision += 1
        elif case == "target_changed":
            existing.target.version += 1
            await ctx.db.commit()
        elif case == "lease_stolen":
            existing.task.lease_token = "other-worker"
            await ctx.db.commit()
        return {"size_bytes": 100, "content_type": "video/mp4", "fingerprint": "f"*64, "duration_seconds": 9.0}

    async def prepare_cover(_locator, destination, _stamp=None):
        produced.append(destination)
        return {"size_bytes": 10, "content_type": "image/png"}

    monkeypatch.setattr(handoff, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(handoff, "_phase", phase)
    monkeypatch.setattr(drama_media, "prepare_drama_video", prepare_video)
    monkeypatch.setattr(drama_media, "prepare_drama_cover", prepare_cover)
    monkeypatch.setattr(storage, "discard_generated", discarded.append)
    if case == "resume_expired_lease":
        existing.task.status, existing.task.lease_token = "running", "dead-worker"
        existing.task.lease_until = now() - timedelta(seconds=1)
        await ctx.db.commit()
    assert await handoff.run_handoff_once() is True
    assert phases == ["copying", "composing", "validating"]
    if case in ("success", "resume_expired_lease"):
        assert existing.task.status == "ready" and existing.target.asset_id is not None
        assert existing.task.lease_token is None and existing.task.lease_until is None
        assert existing.target.cover_asset_id is not None
        assert await ctx.db.scalar(select(func.count()).select_from(Asset)) == 2
        assert discarded == []
    else:
        assert existing.target.asset_id is None and existing.target.cover_asset_id is None
        assert await ctx.db.scalar(select(func.count()).select_from(Asset)) == 0
        assert sorted(discarded) == sorted(produced)
        if case == "lease_stolen":
            assert existing.task.lease_token == "other-worker"  # 旧 worker 不得改新租约。
        else:
            assert existing.task.status == "failed" and ("SOURCE_CHANGED" if case == "source_changed" else "TARGET_CHANGED") in existing.task.error
