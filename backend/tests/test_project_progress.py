"""项目进度保留真实任务来源与当前镜号，所有外部服务使用本地替身。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import asyncio

from app.services import progress
from app.services.billing.context import billing_scope


async def test_progress_copies_payload_and_keeps_task_context_local(monkeypatch):
    monkeypatch.setattr(progress, "_get_redis", lambda: None)
    monkeypatch.setattr(progress, "_queues", {7: []})
    monkeypatch.setattr(progress, "_latest", {})
    queue = progress.subscribe(7)
    payload = {"event": "progress", "shot": 3}
    ready = asyncio.Queue()
    release = asyncio.Event()

    async def publish_in_scope(task_id):
        async with billing_scope(task_id):
            ready.put_nowait(task_id)
            await release.wait()
            await progress.publish_progress(7, payload)

    tasks = [asyncio.create_task(publish_in_scope(task_id)) for task_id in (91, 92)]
    await ready.get()
    await ready.get()
    release.set()
    await asyncio.gather(*tasks)
    await progress.publish_progress(7, payload)
    assert {(await queue.get())["task_id"], (await queue.get())["task_id"]} == {91, 92}
    assert "task_id" not in await queue.get()
    assert payload == {"event": "progress", "shot": 3}


async def test_image_progress_identifies_existing_and_new_shot(db_session, monkeypatch):
    from app.config import get_settings
    from app.models import Project, Shot, Template
    from app.services import pipeline
    from tests.conftest import make_user

    user = await make_user(db_session)
    template = Template(id=f"progress-{user.id}", name="进度测试", category=["科普"], style_prefix="自然")
    db_session.add(template)
    await db_session.flush()
    project = Project(user_id=user.id, template_id=template.id, title="进度", source_text="本地测试", pipeline_mode="full")
    db_session.add(project)
    await db_session.flush()
    db_session.add_all([
        Shot(project_id=project.id, shot_no=2, narration="已有画面", img_prompt="暖壶", image_url="/static/existing.png", duration=3),
        Shot(project_id=project.id, shot_no=7, narration="新画面", img_prompt="杯子", duration=3),
    ])
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    async def image(*_args, **kwargs):
        assert kwargs["shot_no"] == 7
        return SimpleNamespace(local_url="/static/new.png", remote_url="https://synthetic.invalid/new.png")

    async def no_usage(*_args, **_kwargs):
        return None

    received = []

    async def publish(_project_id, payload):
        received.append(payload)

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session)
    monkeypatch.setattr(pipeline, "get_ark", lambda: SimpleNamespace(gen_image=image))
    monkeypatch.setattr(pipeline, "_record_seedream_usage", no_usage)
    monkeypatch.setattr(pipeline, "publish_progress", publish)
    monkeypatch.setattr(get_settings(), "kepu_seedance_native_audio", True)
    await pipeline._parallel_image_and_audio(project.id)
    rows = [row for row in received if row.get("message", "").startswith("出图 ")]
    assert [row["shot"] for row in rows] == [2, 7]
    assert [row["message"].split(" · ")[0] for row in rows] == ["出图 1/2", "出图 2/2"]
