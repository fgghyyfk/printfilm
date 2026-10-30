"""Waiting for image generation must leave database connections available to HTTP reads."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models_teams  # noqa: F401 - resolve team foreign key column types
from app.models import User
from app.models_drama import DramaAsset, DramaProject, DramaScript
from app.services.drama import generation, jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_asset", [True, False])
async def test_asset_image_wait_releases_connection(tmp_path, monkeypatch, existing_asset):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'image.sqlite3').as_posix()}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    image_started = asyncio.Event()
    release_image = asyncio.Event()

    async def generate(*args, **kwargs):
        image_started.set()
        await release_image.wait()
        return SimpleNamespace(local_url="https://cdn.example/new.png", remote_url=None)

    monkeypatch.setattr(jobs, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(jobs, "resolve_visual_prompt_for_asset", AsyncMock(return_value="saved prompt"))
    monkeypatch.setattr(generation, "get_ark", lambda: SimpleNamespace(gen_image=generate))
    monkeypatch.setattr(generation, "resolve_seedream_model_endpoint", lambda _: "image-model")
    monkeypatch.setattr(generation, "resolve_seedream_size", lambda **kwargs: "1536x2048")
    monkeypatch.setattr(generation, "resolve_image_style_board_url", lambda _: None)
    monkeypatch.setattr(generation, "build_generation_prompt", lambda prompt, **kwargs: prompt)
    monkeypatch.setattr(generation, "record_seedream_image_usage", AsyncMock())
    monkeypatch.setattr("app.services.storage.republish_url", lambda url, **kwargs: url)

    worker = None
    try:
        async with engine.begin() as conn:
            for model in (User, DramaProject, DramaScript, DramaAsset):
                await conn.run_sync(model.__table__.create)
        async with sessions() as db:
            user = User(email="image-pool@test.local", hashed_password="test")
            db.add(user)
            await db.flush()
            project = DramaProject(user_id=user.id, title="Pool regression", params={})
            db.add(project)
            await db.flush()
            asset = DramaAsset(project_id=project.id, type="character", name="portrait", params={})
            if existing_asset:
                db.add(asset)
            await db.commit()
            project_id, user_id = project.id, user.id
            asset_id = asset.id if existing_asset else None

        worker = asyncio.create_task(jobs.run_asset_image_job(project_id, user_id, "portrait", asset_id))
        await asyncio.wait_for(image_started.wait(), timeout=3)

        # A separate request must succeed while the provider call is still pending.
        async with sessions() as request_db:
            assert await request_db.scalar(text("SELECT 1")) == 1
            if asset_id:
                saved = await request_db.get(DramaAsset, asset_id)
                assert saved.params["visualPrompt"] == "saved prompt"
                assert saved.params["generation"]["status"] == "generating"

        release_image.set()
        result = await asyncio.wait_for(worker, timeout=3)
        assert result["ok"] is True
        async with sessions() as db:
            saved = await db.scalar(select(DramaAsset).where(DramaAsset.id == result["asset_id"]))
            assert saved.url == "https://cdn.example/new.png"
            assert saved.params["generation"]["status"] == "done"
    finally:
        release_image.set()
        if worker is not None and not worker.done():
            worker.cancel()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        await engine.dispose()
