"""真实流水线+受控HTTP证明独立参考和真实全局并发上限，不调用真实上游。"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import get_settings
from app.models import Shot
from app.services import pipeline, tokenfree_image
from app.services.ark import ArkGateway
from tests.test_kepu_task_recovery import seed_project


async def setup_project(db, monkeypatch, mode):
    _, project, first = await seed_project(db)
    project.image_reference_mode = mode
    first.image_url = "https://cdn.test/existing.png"
    for no in (2, 3):
        db.add(Shot(project_id=project.id, shot_no=no, img_prompt=f"画面{no}", narration=""))
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    monkeypatch.setattr(pipeline, "_project_is_avatar", AsyncMock(return_value=False))
    monkeypatch.setattr(pipeline, "_set_status", AsyncMock())
    monkeypatch.setattr(pipeline, "_ensure_not_cancelled", AsyncMock())
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(pipeline, "project_image_references", lambda _: (["https://cdn.test/base.png"], []))
    usage = AsyncMock()
    monkeypatch.setattr(pipeline, "_record_seedream_usage", usage)
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", True)
    monkeypatch.setattr(get_settings(), "pipeline_image_concurrency", 3)
    return project.id, usage


@pytest.mark.parametrize("mode,cap,base,peak", [
    ("independent", 1, "https://ark.cn-beijing.volces.com/api/v3", 1),
    ("independent", 2, "https://www.tokenfree.com/v1", 2),
    ("chain", 2, "https://www.tokenfree.com/v1", 1),
])
async def test_pipeline_honors_reference_mode_and_real_http_slot(db_session, monkeypatch, tmp_path, mode, cap, base, peak):
    pid, usage = await setup_project(db_session, monkeypatch, mode)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "tokenfree_image_concurrency", cap)
    monkeypatch.setattr(cfg, "ark_base_url", base)
    monkeypatch.setattr(tokenfree_image, "_image_slot", None)
    gateway = ArkGateway(cfg)
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *_: {})
    entered, release = asyncio.Event(), asyncio.Event()
    active = maximum = posts = 0
    refs = []

    async def handler(request):
        nonlocal active, maximum, posts
        assert request.method == "POST"
        posts += 1
        index = posts
        active += 1
        maximum = max(maximum, active)
        if active == peak:
            entered.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json={"data": [{"url": f"https://cdn.test/new{index}.png"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("app.services.ark.httpx.AsyncClient", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
    async def download(url, path, **kw):
        path.write_bytes(b"controlled image")
    monkeypatch.setattr("app.services.ark.storage.download_to", download)
    monkeypatch.setattr("app.services.ark.storage.project_dir", lambda _: tmp_path)
    monkeypatch.setattr("app.services.ark.storage.publish_local", lambda path, **kw: str(path))
    async def image(prompt, negative, references, **kw):
        refs.append(list(references))
        return await gateway._seedream_once(prompt, references, **{k: v for k, v in kw.items() if k != "model"}, model="seedream-5-0-pro")
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(gen_image=image))
    job = asyncio.create_task(pipeline._parallel_image_and_audio(pid))
    await asyncio.wait_for(entered.wait(), 5)
    assert maximum == peak
    release.set()
    await asyncio.wait_for(job, 5)
    assert posts == 2 and maximum == peak and usage.await_count == 2
    if mode == "independent":
        assert refs == [["https://cdn.test/base.png"], ["https://cdn.test/base.png"]]
    else:
        assert refs[0][0] == "https://cdn.test/existing.png"
        assert refs[1][0] == "https://cdn.test/new1.png"


async def test_independent_failure_waits_for_other_paid_result(db_session, monkeypatch):
    pid, usage = await setup_project(db_session, monkeypatch, "independent")
    monkeypatch.setattr(get_settings(), "tokenfree_image_concurrency", 2)
    both, release = asyncio.Event(), asyncio.Event()
    started = 0
    async def image(*args, shot_no, **kw):
        nonlocal started
        started += 1
        if started == 2:
            both.set()
        await both.wait()
        if shot_no == 2:
            raise RuntimeError("controlled first failure")
        await release.wait()
        return SimpleNamespace(local_url="/static/paid-finished.png", remote_url="https://cdn.test/paid.png")
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(gen_image=image))
    job = asyncio.create_task(pipeline._parallel_image_and_audio(pid))
    await asyncio.wait_for(both.wait(), 5)
    await asyncio.sleep(0)
    assert not job.done()
    release.set()
    with pytest.raises(RuntimeError, match="controlled first failure"):
        await asyncio.wait_for(job, 5)
    assert usage.await_count == 1
