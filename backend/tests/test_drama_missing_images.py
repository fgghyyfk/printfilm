"""一键补缺图使用实际报价，并避免已出图或跨窗口重复入队。"""
from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.drama.generation import generate_image
from app.models_drama import DramaAsset, DramaProject
from app.config import get_settings
from app.models import User
from app.models_tasks import TaskRun
from app.schemas_drama import DramaImageGenerateRequest
from app.services.billing.estimates import estimate_task_fen
from tests.conftest import make_task, make_user


# 创建真实项目与尚未出图的角色，API仍执行真实归属和任务创建。
async def image_context(db):
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title="补图测试")
    db.add(project)
    await db.flush()
    asset = DramaAsset(project_id=project.id, name="角色甲", type="character", params={})
    db.add(asset)
    await db.commit()
    return user, project, asset


# 请求仍复用原有逐资产生图接口，只有批量补缺显式标记missing_only。
def image_body(project, asset, **overrides):
    return DramaImageGenerateRequest(
        project_id=project.id, asset_id=asset.id, prompt="角色甲参考形象",
        **{"model_id": "gpt-image-2", "resolution": "2K", "missing_only": True, **overrides},
    )


@pytest.mark.parametrize("model,expected", [("seedream-4.5", 10), ("gpt-image-2", 70)])
async def test_image_estimate_uses_selected_model(db_session, monkeypatch, model, expected):
    """同样尺寸的不同模型，估价必须取所选模型而非系统默认价。"""
    from app.services.billing import estimates

    monkeypatch.setattr(estimates, "charge_fen_official_image", lambda settings, *, model, size: {
        "seedream-4.5": 10, "gpt-image-2": 70,
    }.get(model, 3))
    task = TaskRun(domain="drama", task_type="asset_image", payload={"model_id": model, "resolution": "2K"})
    assert await estimate_task_fen(db_session, task) == expected


@pytest.mark.parametrize("model,resolution,expected", [
    ("seedream-4.5", "2K", 10), ("gpt-image-2", "2K", 70), ("gpt-image-2", "1K", 40),
])
async def test_preflight_matches_enqueued_and_frozen_image_price(db_session, monkeypatch, model, resolution, expected):
    """真实HTTP预检、入队和执行预扣共享所选模型，不新增费率。"""
    import httpx
    from fastapi import FastAPI
    from app.api import billing
    from app.services.billing import estimates
    from app.services.billing.settlement import freeze_for_task

    user, project, asset = await image_context(db_session)
    monkeypatch.setattr(estimates, "charge_fen_official_image", lambda settings, *, model, size: {
        ("seedream-4.5", "2K"): 10, ("gpt-image-2", "2K"): 70, ("gpt-image-2", "1K"): 40,
    }.get((model, size), 3))
    app = FastAPI()
    app.include_router(billing.router, prefix="/api")
    app.dependency_overrides[billing.get_db] = lambda: db_session
    app.dependency_overrides[billing.get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/billing/preflight", params={
            "domain": "drama", "task_type": "asset_image", "count": 2,
            "model_id": model, "resolution": resolution, "aspect_ratio": "3:4",
        })
    assert response.status_code == 200
    assert response.json()["unit_estimate_fen"] == expected
    assert response.json()["requested_total_fen"] == expected * 2
    result = await generate_image(image_body(project, asset, model_id=model, resolution=resolution), db_session, user)
    task = await db_session.get(TaskRun, int(result["task_id"]))
    assert task.billing_estimate_fen == expected
    assert await freeze_for_task(db_session, task) == expected


async def test_missing_only_preserves_existing_image_and_generation(db_session):
    """跨窗口已生成成功的角色被跳过，不写queued或新增费用任务。"""
    user, project, asset = await image_context(db_session)
    asset.cover = asset.url = "/static/generated/existing.png"
    asset.params = {"generation": {"status": "done", "message": "保留结果"}}
    await db_session.commit()

    result = await generate_image(image_body(project, asset), db_session, user)

    assert result["queued"] is False
    assert result["skipped"] is True
    assert result["asset"]["url"] == asset.url
    assert asset.params == {"generation": {"status": "done", "message": "保留结果"}}
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.asset_id == asset.id)) == 0


@pytest.mark.parametrize("status", ["pending", "running", "cancel_requested", "awaiting_review"])
async def test_missing_only_reuses_inflight_task(db_session, status):
    """刷新/另一标签重复补缺只接回现有活动任务，不依赖本地队列。"""
    user, project, asset = await image_context(db_session)
    task = await make_task(db_session, user, domain="drama", task_type="asset_image", status=status)
    task.drama_project_id = project.id
    task.asset_id = asset.id
    asset.params = {"generation": {"status": "generating", "message": "保留进度"}}
    await db_session.commit()

    result = await generate_image(image_body(project, asset), db_session, user)

    assert int(result["task_id"]) == task.id
    assert result["status"] == status
    assert asset.params["generation"]["message"] == "保留进度"
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.asset_id == asset.id)) == 1


async def test_explicit_redraw_keeps_original_behavior(db_session):
    """单项显式重绘仍允许已有图入队，不受补缺开关影响。"""
    user, project, asset = await image_context(db_session)
    asset.cover = asset.url = "/static/existing.png"
    await db_session.commit()
    result = await generate_image(image_body(project, asset, missing_only=False), db_session, user)
    assert result["queued"] is True
    task = await db_session.get(TaskRun, int(result["task_id"]))
    assert task.payload["model_id"] == "gpt-image-2"
    assert asset.url == "/static/existing.png"


async def test_missing_only_rejects_foreign_asset(db_session):
    """补缺标记不会放宽项目和资产归属。"""
    user, project, asset = await image_context(db_session)
    other = DramaProject(user_id=user.id, title="另一个项目")
    db_session.add(other)
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await generate_image(image_body(other, asset), db_session, user)
    assert error.value.status_code == 404


@pytest.mark.parametrize("race", ["duplicate_submit", "image_during_quote"])
async def test_missing_only_rechecks_under_lock_after_price_preparation(db_session, monkeypatch, race):
    """真实独立连接交错：冷价目不锁资产，重复标签只创建一条收费任务。"""
    from app.api.drama import generation
    from app.services.billing import estimates

    # fixture只初始化隔离库；竞态数据在独立连接提交，最后显式清理。
    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered, resume = asyncio.Event(), asyncio.Event()
    pending = []
    user_id = project_id = None
    try:
        async with sessions() as setup:
            user, project, asset = await image_context(setup)
            user_id, project_id, asset_id = user.id, project.id, asset.id
        if race == "duplicate_submit":
            dispatch = generation.dispatch_asset_image_job

            async def pause_dispatch(*args, **kwargs):
                """第一请求锁住资产并停在真实建任务之前。"""
                if not entered.is_set():
                    entered.set()
                    await resume.wait()
                return await dispatch(*args, **kwargs)

            monkeypatch.setattr(generation, "dispatch_asset_image_job", pause_dispatch)
        else:
            official_rates = estimates.ensure_official_rates

            async def pause_rates(*args, **kwargs):
                """模拟冷价目等待，另一连接必须仍可提交新图片。"""
                entered.set()
                await resume.wait()
                return await official_rates(*args, **kwargs)

            monkeypatch.setattr(estimates, "ensure_official_rates", pause_rates)

        async def request():
            """每个标签使用独立会话、真实归属校验与TaskRun创建。"""
            async with sessions() as db:
                user = await db.get(User, user_id)
                return await generate_image(DramaImageGenerateRequest(
                    project_id=project_id, asset_id=asset_id, prompt="角色参考图", missing_only=True,
                ), db, user)

        first = asyncio.create_task(request())
        pending.append(first)
        await asyncio.wait_for(entered.wait(), 3)
        if race == "duplicate_submit":
            second = asyncio.create_task(request())
            pending.append(second)
            await asyncio.wait({second}, timeout=0.1)
            assert not second.done(), "第二标签应等待同一资产的短行锁"
        else:
            async with sessions() as update:
                current = await update.get(DramaAsset, asset_id)
                current.cover = current.url = "/static/new-reference.png"
                current.params = {"generation": {"status": "done"}}
                await asyncio.wait_for(update.commit(), 2)
        resume.set()
        results = await asyncio.wait_for(asyncio.gather(*pending), 5)
        async with sessions() as check:
            count = await check.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.asset_id == asset_id))
            if race == "duplicate_submit":
                assert results[0]["task_id"] == results[1]["task_id"]
                assert results[1]["reused"] is True
                assert count == 1
            else:
                assert results[0]["skipped"] is True
                assert results[0]["asset"]["url"] == "/static/new-reference.png"
                assert count == 0
    finally:
        resume.set()
        for task in pending:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if project_id is not None:
            async with sessions() as cleanup:
                await cleanup.execute(delete(TaskRun).where(TaskRun.requested_by == user_id))
                await cleanup.execute(delete(DramaAsset).where(DramaAsset.project_id == project_id))
                await cleanup.execute(delete(DramaProject).where(DramaProject.id == project_id))
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.commit()
        await engine.dispose()
