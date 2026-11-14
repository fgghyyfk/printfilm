"""公开播放策略：测试库与 OSS 传输替身，不代表真实云端验收。"""
from datetime import timedelta

from sqlalchemy import select
import pytest

from app.marketplace import storage
from app.marketplace.models import EpisodePreview, Publication, Series, SeriesEpisode, Work, WorkPreview
from app.marketplace.previews import CLIP_EPISODE_PREFIX
from app.marketplace.service import now
from test_marketplace import asset, ctx, fake_oss, rights, work_body  # noqa: F401


async def make_series(ctx, *, sale_type="copyright_contact", free_count=0, count=2, public=True):
    cover = await asset(ctx, kind="image")
    series = Series(owner_id=ctx.owner.id, title="策略测试短剧", category="其他", sale_type=sale_type,
                    free_episode_count=free_count, cover_asset_id=cover.id,
                    audit_status="approved" if public else "draft", listed=public)
    ctx.db.add(series)
    await ctx.db.flush()
    episodes = []
    for number in range(1, count + 1):
        video = await asset(ctx)
        await rights(ctx, video, full=True)
        episode = SeriesEpisode(owner_id=ctx.owner.id, series_id=series.id, episode_no=number,
                                title=f"第{number}集", source="original", asset_id=video.id,
                                audit_status="approved" if public else "draft", listed=public)
        ctx.db.add(episode)
        await ctx.db.flush()
        episodes.append((episode, video))
    return series, episodes


async def test_explicit_free_work_submits_without_preview_and_plays_original(ctx, monkeypatch):
    video = await asset(ctx)
    await rights(ctx, video, full=True)
    created = await ctx.client.post("/api/marketplace/works", json={**work_body(video), "sale_type": "free"})
    assert created.status_code == 200, created.text
    work_id = created.json()["id"]
    path = f"/api/marketplace/works/{work_id}"
    submitted = await ctx.client.post(path + "/submit", json={"version": 1})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["audit_status"] == "pending"
    assert submitted.json()["preview_status"] == "none"
    assert not (await ctx.db.execute(select(WorkPreview.id))).scalars().all()
    publication = (await ctx.db.execute(select(Publication).where(Publication.content_id == work_id))).scalar_one()
    assert publication.preview_status == "none" and publication.preview_key is None
    ctx.user = ctx.admin
    approved = await ctx.client.post(f"/api/admin/marketplace/works/{work_id}/review", json={
        "version": 1, "decision": "approved", "reason": "内容和版权已核验", "checks": ["content", "copyright"]})
    assert approved.status_code == 200, approved.text
    ctx.user = ctx.owner
    assert (await ctx.client.post(path + "/listing", json={"version": 1})).status_code == 200
    fake_oss(monkeypatch)
    signed = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: signed.append(key) or "https://test.invalid/free")
    ctx.user = None
    playback = await ctx.client.get(f"/api/marketplace/media/work/{work_id}/preview")
    assert playback.status_code == 200, playback.text
    assert playback.json() == {"url": "https://test.invalid/free", "expires_in": 300}
    assert signed == [video.object_key]


@pytest.mark.parametrize("kind", ["work", "series"])
async def test_zero_reference_price_does_not_infer_free_from_missing_sale_type(ctx, kind):
    body = {"title": "参考价未定"}
    if kind == "work":
        body.update(work_body(), price_fen=0)
    else:
        body["copyright_price_fen"] = 0
    response = await ctx.client.post(f"/api/marketplace/{'works' if kind == 'work' else 'series'}", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["sale_type"] == "copyright_contact"


@pytest.mark.parametrize("legacy_key", ["marketplace/work-watermarks-full/old.mp4", "marketplace/work-previews-hq/old.mp4"])
async def test_legacy_work_files_are_not_signed_or_reused_at_submission(ctx, monkeypatch, legacy_key):
    fake_oss(monkeypatch)
    calls = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: calls.append(key) or "https://test.invalid/media")
    video = await asset(ctx)
    await rights(ctx, video, full=True)
    work = Work(owner_id=ctx.owner.id, **{**work_body(video), "sale_type": "copyright_contact", "price_fen": 0},
                audit_status="approved", listed=True)
    ctx.db.add(work)
    await ctx.db.flush()
    old = WorkPreview(work_id=work.id, asset_id=video.id, version=1, status="done", object_key=legacy_key)
    ctx.db.add(old)
    await ctx.db.flush()
    media = f"/api/marketplace/media/work/{work.id}/preview"
    ctx.user = None
    assert (await ctx.client.get(media)).status_code == 404
    ctx.user = ctx.owner
    assert (await ctx.client.get(media)).status_code == 409
    assert calls == []
    work.audit_status, work.listed = "draft", False
    await ctx.db.flush()
    submitted = await ctx.client.post(f"/api/marketplace/works/{work.id}/submit", json={"version": 1})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["audit_status"] == "processing"
    assert old.status == "pending" and old.object_key is None


async def test_free_to_paid_never_falls_back_to_source(ctx, monkeypatch):
    fake_oss(monkeypatch)
    video = await asset(ctx)
    await rights(ctx, video, full=True)
    work = Work(owner_id=ctx.owner.id, **{**work_body(video), "sale_type": "free"}, audit_status="approved", listed=True)
    ctx.db.add(work)
    await ctx.db.flush()
    calls = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: calls.append(key) or "https://test.invalid/media")
    media = f"/api/marketplace/media/work/{work.id}/preview"
    ctx.user = None
    assert (await ctx.client.get(media)).status_code == 200
    assert calls == [video.object_key]
    ctx.user = ctx.owner
    changed = await ctx.client.put(f"/api/marketplace/works/{work.id}", json={
        **work_body(video), "sale_type": "copyright_contact", "price_fen": 0, "version": 1})
    assert changed.status_code == 200, changed.text
    ctx.user = None
    assert (await ctx.client.get(media)).status_code == 404
    # Even corrupt legacy listing flags cannot permit a source fallback.
    work.audit_status, work.listed = "approved", True
    await ctx.db.flush()
    assert (await ctx.client.get(media)).status_code == 404
    ctx.user = ctx.owner
    assert (await ctx.client.get(media)).status_code == 409
    assert calls == [video.object_key]


@pytest.mark.parametrize("sale_type,free_count", [("free", 0), ("copyright_contact", 1)])
async def test_series_public_playback_respects_full_free_and_first_n_free(ctx, monkeypatch, sale_type, free_count):
    fake_oss(monkeypatch)
    series, episodes = await make_series(ctx, sale_type=sale_type, free_count=free_count)
    paid = sale_type != "free"
    if paid:
        episode, video = episodes[1]
        ctx.db.add(EpisodePreview(episode_id=episode.id, asset_id=video.id, version=episode.version,
                                 status="done", object_key=CLIP_EPISODE_PREFIX + "paid.mp4"))
        await ctx.db.flush()
    calls = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: calls.append(key) or "https://test.invalid/media")
    ctx.user = None
    detail = await ctx.client.get(f"/api/marketplace/series/{series.id}")
    assert detail.status_code == 200, detail.text
    for index, (episode, video) in enumerate(episodes):
        response = await ctx.client.get(f"/api/marketplace/media/series_episode/{episode.id}/preview")
        assert response.status_code == 200, response.text
        assert calls[-1] == (CLIP_EPISODE_PREFIX + "paid.mp4" if paid and index == 1 else video.object_key)


async def test_free_series_submits_and_reviews_without_any_preview_jobs(ctx):
    series, episodes = await make_series(ctx, sale_type="free", public=False)
    submitted = await ctx.client.post(f"/api/marketplace/series/{series.id}/submit", json={"version": series.version})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["audit_status"] == "pending"
    assert all(row["preview_status"] == "none" for row in submitted.json()["episodes"])
    assert not (await ctx.db.execute(select(EpisodePreview.id))).scalars().all()
    ctx.user = ctx.admin
    approved = await ctx.client.post(f"/api/admin/marketplace/series/{series.id}/review", json={
        "version": series.version, "decision": "approved", "reason": "内容与版权通过", "checks": ["content", "copyright"]})
    assert approved.status_code == 200, approved.text
    ctx.user = ctx.owner
    listed = await ctx.client.post(f"/api/marketplace/series/{series.id}/listing", json={"version": series.version})
    assert listed.status_code == 200, listed.text


async def test_mixed_series_submission_only_queues_paid_episode(ctx, monkeypatch):
    fake_oss(monkeypatch)
    series, episodes = await make_series(ctx, free_count=1, public=False)
    submitted = await ctx.client.post(f"/api/marketplace/series/{series.id}/submit", json={"version": series.version})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["audit_status"] == "processing"
    assert [row["preview_status"] for row in submitted.json()["episodes"]] == ["none", "pending"]
    jobs = (await ctx.db.execute(select(EpisodePreview))).scalars().all()
    assert len(jobs) == 1 and jobs[0].episode_id == episodes[1][0].id
    repeated = await ctx.client.post(f"/api/marketplace/series/{series.id}/submit", json={"version": series.version})
    assert repeated.status_code == 200
    assert len((await ctx.db.execute(select(EpisodePreview))).scalars().all()) == 1


@pytest.mark.parametrize("restriction", ["series", "episode", "rights", "expired", "deleted"])
async def test_free_series_restrictions_prevent_original_signing(ctx, monkeypatch, restriction):
    from app.marketplace.models import Rights

    fake_oss(monkeypatch)
    series, episodes = await make_series(ctx, sale_type="free", count=1)
    episode, video = episodes[0]
    if restriction == "series":
        series.blocked = True
    elif restriction == "episode":
        episode.blocked = True
    elif restriction == "deleted":
        series.deleted_at = now()
    else:
        proof = (await ctx.db.execute(select(Rights).where(Rights.asset_id == video.id))).scalar_one()
        if restriction == "rights":
            proof.blocked = True
        else:
            proof.valid_until = now() - timedelta(seconds=1)
    await ctx.db.flush()
    calls = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: calls.append(key) or "https://test.invalid/media")
    ctx.user = None
    assert (await ctx.client.get(f"/api/marketplace/media/series_episode/{episode.id}/preview")).status_code in (403, 404)
    assert calls == []


@pytest.mark.parametrize("legacy_key", ["marketplace/series-watermarks-full/old.mp4", "marketplace/series-previews-hq/old.mp4"])
async def test_paid_series_rejects_legacy_full_and_old_clip(ctx, monkeypatch, legacy_key):
    fake_oss(monkeypatch)
    series, episodes = await make_series(ctx, count=1)
    episode, video = episodes[0]
    ctx.db.add(EpisodePreview(episode_id=episode.id, asset_id=video.id, version=episode.version,
                             status="done", object_key=legacy_key))
    await ctx.db.flush()
    calls = []
    monkeypatch.setattr(storage, "signed", lambda key, mime: calls.append(key) or "https://test.invalid/media")
    ctx.user = None
    assert (await ctx.client.get(f"/api/marketplace/series/{series.id}")).status_code == 404
    assert (await ctx.client.get(f"/api/marketplace/media/series_episode/{episode.id}/preview")).status_code in (404, 409)
    ctx.user = ctx.owner
    assert (await ctx.client.get(f"/api/marketplace/media/series_episode/{episode.id}/preview")).status_code == 409
    assert calls == []


async def test_old_version_clip_cannot_satisfy_current_episode(ctx, monkeypatch):
    fake_oss(monkeypatch)
    series, episodes = await make_series(ctx, count=1)
    episode, video = episodes[0]
    ctx.db.add(EpisodePreview(episode_id=episode.id, asset_id=video.id, version=episode.version,
                             status="done", object_key=CLIP_EPISODE_PREFIX + "old-version.mp4"))
    episode.version += 1
    await ctx.db.flush()
    ctx.user = ctx.owner
    assert (await ctx.client.get(f"/api/marketplace/media/series_episode/{episode.id}/preview")).status_code == 409


async def test_policy_reconciliation_cancels_free_jobs_and_preserves_historical_playback(ctx):
    from app.marketplace.previews import reconcile_preview_policy

    video = await asset(ctx)
    await rights(ctx, video, full=True)
    work = Work(owner_id=ctx.owner.id, **{**work_body(video), "sale_type": "free"}, audit_status="processing")
    ctx.db.add(work)
    await ctx.db.flush()
    job = WorkPreview(work_id=work.id, asset_id=video.id, version=work.version, status="running",
                      lease_token="old-worker", lease_until=now() + timedelta(minutes=1), attempts=1)
    old_publication = Publication(content_type="work", content_id=work.id, owner_id=ctx.owner.id,
                                  version=0, asset_id=video.id, sale_type="platform_unlock",
                                  audit_status="approved", preview_status="done",
                                  preview_key="marketplace/work-watermarks-full/old.mp4",
                                  playback_status="done", playback_manifest_key="marketplace/hls/old/index.m3u8")
    ctx.db.add_all([job, old_publication])
    await ctx.db.flush()
    await reconcile_preview_policy(ctx.db)
    await ctx.db.flush()
    assert job.status == "cancelled" and job.lease_token is None and job.lease_until is None
    assert work.audit_status == "pending" and not work.listed
    assert old_publication.asset_id == video.id
    assert old_publication.preview_key == "marketplace/work-watermarks-full/old.mp4"
    assert old_publication.playback_manifest_key == "marketplace/hls/old/index.m3u8"
    assert old_publication.audit_status == "approved"
    await reconcile_preview_policy(ctx.db)
    assert job.status == "cancelled" and work.audit_status == "pending"


async def test_policy_reconciliation_does_not_advance_blocked_free_work(ctx):
    from app.marketplace.previews import reconcile_preview_policy

    video = await asset(ctx)
    proof = await rights(ctx, video, full=True)
    proof.blocked = True
    work = Work(owner_id=ctx.owner.id, **{**work_body(video), "sale_type": "free"}, audit_status="processing")
    ctx.db.add(work)
    await ctx.db.flush()
    job = WorkPreview(work_id=work.id, asset_id=video.id, version=work.version, status="pending")
    ctx.db.add(job)
    await ctx.db.flush()
    await reconcile_preview_policy(ctx.db)
    assert work.audit_status != "pending" and not work.listed
    assert job.status == "cancelled"


@pytest.mark.parametrize("kind", ["work", "episode"])
@pytest.mark.parametrize("state", ["terminal_v2", "running_v2", "running_legacy"])
async def test_reconciliation_preserves_current_v2_attempts_and_revokes_legacy(ctx, kind, state):
    from app.marketplace.previews import reconcile_preview_policy

    if kind == "work":
        video = await asset(ctx)
        await rights(ctx, video, full=True)
        content = Work(owner_id=ctx.owner.id, **work_body(video), audit_status="processing")
        ctx.db.add(content)
        await ctx.db.flush()
        job = WorkPreview(work_id=content.id, asset_id=video.id, version=content.version)
        parent = content
    else:
        parent, episodes = await make_series(ctx, count=1, public=False)
        parent.audit_status = "processing"
        content, video = episodes[0]
        job = EpisodePreview(episode_id=content.id, asset_id=video.id, version=content.version)
    job.status = "failed" if state == "terminal_v2" else "running"
    job.attempts = 3 if state == "terminal_v2" else 1
    job.error = "截取失败，可重试" if state == "terminal_v2" else ""
    job.lease_token = "old-watermark-worker" if state == "running_legacy" else "preview-v2:worker-token"
    lease = now() + timedelta(minutes=1)
    job.lease_until = None if state == "terminal_v2" else lease
    ctx.db.add(job)
    await ctx.db.flush()
    for _ in range(2):
        await reconcile_preview_policy(ctx.db)
        await ctx.db.flush()
        if state == "terminal_v2":
            assert job.status == "failed" and job.attempts == 3
            assert job.lease_token == "preview-v2:worker-token"
            assert parent.audit_status == "preview_failed"
        elif state == "running_v2":
            assert job.status == "running" and job.attempts == 1
            assert job.lease_token == "preview-v2:worker-token" and job.lease_until == lease
            assert parent.audit_status == "processing"
        else:
            assert job.status == "pending" and job.attempts == 0
            assert job.lease_token is None and job.lease_until is None


async def test_reconciliation_does_not_apply_old_version_terminal_error_to_current_work(ctx):
    from app.marketplace.previews import reconcile_preview_policy

    video = await asset(ctx)
    await rights(ctx, video, full=True)
    work = Work(owner_id=ctx.owner.id, **work_body(video), version=2, audit_status="processing")
    ctx.db.add(work)
    await ctx.db.flush()
    old = WorkPreview(work_id=work.id, asset_id=video.id, version=1, status="failed", attempts=3,
                      lease_token="preview-v2:old-version", error="旧版本失败")
    ctx.db.add(old)
    await ctx.db.flush()
    await reconcile_preview_policy(ctx.db)
    assert work.audit_status == "processing"
    assert old.version == work.version and old.status == "pending" and old.attempts == 0


async def retry_content(ctx, kind, *, free=False):
    if kind == "work":
        video = await asset(ctx)
        await rights(ctx, video, full=True)
        content = Work(owner_id=ctx.owner.id, **{**work_body(video), "sale_type": "free" if free else "copyright_contact"},
                       audit_status="approved", listed=True)
        ctx.db.add(content)
        await ctx.db.flush()
        path = f"/api/marketplace/works/{content.id}/preview/retry"
        job = WorkPreview(work_id=content.id, asset_id=video.id, version=content.version)
    else:
        parent, episodes = await make_series(ctx, count=1, sale_type="free" if free else "copyright_contact")
        content, video = episodes[0]
        path = f"/api/marketplace/series/{parent.id}/preview/retry"
        job = EpisodePreview(episode_id=content.id, asset_id=video.id, version=content.version)
    publication = Publication(content_type="work" if kind == "work" else "series_episode", content_id=content.id,
                              owner_id=ctx.owner.id, version=content.version, asset_id=video.id,
                              sale_type="free" if free else "copyright_contact", audit_status="approved", listed=True,
                              copyright_price_fen=12345, unlock_price_fen=678, share_bps=2345,
                              preview_status="done", preview_key="marketplace/legacy/preview.mp4",
                              playback_status="done", playback_manifest_key="marketplace/hls/old/index.m3u8")
    ctx.db.add(publication)
    if not free:
        job.status, job.attempts, job.error = "failed", 3, "上传失败，可重试"
        job.lease_token = "preview-v2:failed-token"
        ctx.db.add(job)
    await ctx.db.flush()
    return path, content, publication, job


@pytest.mark.parametrize("kind", ["work", "episode"])
async def test_preview_retry_owner_only_and_idempotent_preserves_approved_publication(ctx, monkeypatch, kind):
    path, content, publication, job = await retry_content(ctx, kind)
    ctx.user = None
    assert (await ctx.client.post(path, json={"version": 1})).status_code == 401
    ctx.user = ctx.stranger
    assert (await ctx.client.post(path, json={"version": 1})).status_code == 404
    assert job.status == "failed" and job.attempts == 3
    ctx.user = ctx.owner
    missing = await ctx.client.post(path, json={"version": 1})
    assert missing.status_code == 503, missing.text
    assert job.status == "failed" and job.attempts == 3
    fake_oss(monkeypatch)
    stale = await ctx.client.post(path, json={"version": 99})
    assert stale.status_code == 409
    for _ in range(2):
        retried = await ctx.client.post(path, json={"version": 1})
        assert retried.status_code == 200, retried.text
        assert job.status == "pending" and job.attempts == 0 and job.lease_token is None
        assert content.audit_status == "approved" and content.listed
        assert publication.audit_status == "approved" and publication.listed
        assert publication.preview_key == "marketplace/legacy/preview.mp4"
        assert publication.playback_manifest_key == "marketplace/hls/old/index.m3u8"
        assert publication.copyright_price_fen == 12345 and publication.unlock_price_fen == 678 and publication.share_bps == 2345
    lease = now() + timedelta(minutes=1)
    job.status, job.attempts, job.lease_token, job.lease_until = "running", 1, "preview-v2:live-retry", lease
    await ctx.db.flush()
    in_progress = await ctx.client.post(path, json={"version": 1})
    assert in_progress.status_code == 200, in_progress.text
    assert job.status == "running" and job.attempts == 1
    assert job.lease_token == "preview-v2:live-retry" and job.lease_until == lease
    model = WorkPreview if kind == "work" else EpisodePreview
    assert len((await ctx.db.execute(select(model))).scalars().all()) == 1


@pytest.mark.parametrize("kind", ["work", "episode"])
async def test_free_content_retry_never_creates_preview_job(ctx, monkeypatch, kind):
    path, content, publication, _job = await retry_content(ctx, kind, free=True)
    fake_oss(monkeypatch)
    response = await ctx.client.post(path, json={"version": 1})
    assert response.status_code == 200, response.text
    model = WorkPreview if kind == "work" else EpisodePreview
    assert not (await ctx.db.execute(select(model))).scalars().all()
    assert content.audit_status == "approved" and content.listed
    assert publication.preview_key == "marketplace/legacy/preview.mp4"
