"""E6：跨表分页、来源隔离与离线带货分镜。"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Project, Shot, Template
from app.models_ecom import EcomJob, EcomJobItem, EcomSet
from app.models_tasks import TaskRun
from app.services import storage
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID
from tests.conftest import make_user


# 构造当前用户的成功与失败图片集合。
async def seed_job(db, user, tmp_path, count=2, kind="style"):
    job = EcomJob(user_id=user.id, kind=kind, title="保温杯", status="partial", params={"clarity": "2k"})
    db.add(job)
    await db.flush()
    for idx in range(count + 1):
        path = tmp_path / f"result_{idx}.png"
        Image.new("RGB", (40, 40), "blue").save(path)
        db.add(EcomJobItem(job_id=job.id, idx=idx, status="succeeded" if idx < count else "failed",
                           result_url=storage.rel_static_url(path)))
    await db.commit()
    await db.refresh(job)
    return job


# 跨表先合并再分页，筛选与总数隔离当前用户，同时间保持稳定次序。
async def test_aggregate_pagination_and_filter(db_session):
    from app.services.ecom_hub import list_recent

    user, other = await make_user(db_session), await make_user(db_session)
    now = datetime.now(timezone.utc)
    db_session.add_all([
        EcomSet(user_id=user.id, name="套图", created_at=now, status="planned"),
        EcomJob(user_id=user.id, kind="style", created_at=now, title="复刻"),
        EcomJob(user_id=user.id, kind="refine", created_at=now - timedelta(days=1)),
        EcomSet(user_id=other.id, name="不可见", created_at=now + timedelta(days=1)),
    ])
    await db_session.flush()
    first = await list_recent(db_session, user, page=1, page_size=1, kind=None)
    second = await list_recent(db_session, user, page=2, page_size=1, kind=None)
    again = await list_recent(db_session, user, page=1, page_size=1, kind=None)
    assert first["meta"] == {"page": 1, "page_size": 1, "total": 3}
    assert first == again and first["items"] != second["items"]
    assert {first["items"][0]["kind"], second["items"][0]["kind"]} == {"set", "style"}
    filtered = await list_recent(db_session, user, page=1, page_size=12, kind="refine")
    assert filtered["meta"]["total"] == 1 and filtered["items"][0]["kind"] == "refine"
    assert (await list_recent(db_session, user, page=9, page_size=12, kind="set"))["items"] == []
    with pytest.raises(ValueError):
        await list_recent(db_session, user, page=1, page_size=12, kind="unknown")


# 所有类型的成功图可送入套图与参考图入口，拒绝失败项及越权。
@pytest.mark.parametrize("kind", ["refine", "style", "buyer_show", "sku", "translate"])
async def test_result_sources(db_session, monkeypatch, tmp_path, kind):
    from app.services.ecom_jobs.sources import result_path, source_uploads
    from app.services.ecom_set.sources import refine_product

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    user, other = await make_user(db_session), await make_user(db_session)
    job = await seed_job(db_session, user, tmp_path, kind=kind)
    path = await result_path(db_session, user, job.id, 0)
    assert path.is_file() and await refine_product(db_session, user, job.id, 0) == path
    uploads = await source_uploads(db_session, user, job.id, 0)
    assert len(uploads) == 1 and uploads[0][0]
    with pytest.raises(LookupError):
        await result_path(db_session, other, job.id, 0)
    with pytest.raises(ValueError):
        await result_path(db_session, user, job.id, 2)


# 报价不建任务，重复请求幂等，新建保留旧项目，mock 复用图片生成分镜。
async def test_job_to_daihuo_offline(db_session, monkeypatch, tmp_path):
    from app.services.daihuo.job_source import create_project_from_job, quote_project_from_job
    from app.services.daihuo.service import build_daihuo_shots, project_has_ecom_set

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    user, other = await make_user(db_session), await make_user(db_session)
    if not await db_session.get(Template, DAIHUO_TEMPLATE_ID):
        db_session.add(Template(id=DAIHUO_TEMPLATE_ID, name="带货", style_prefix=""))
    job = await seed_job(db_session, user, tmp_path)
    before = (await db_session.scalar(select(func.count(Project.id))), await db_session.scalar(select(func.count(TaskRun.id))))
    quote = await quote_project_from_job(db_session, user, job.id, output_ratio="16:9", voice_id="voice-a")
    assert quote["usable_images"] == 2 and quote["image_fen"] == 0
    assert before == (await db_session.scalar(select(func.count(Project.id))), await db_session.scalar(select(func.count(TaskRun.id))))
    project = await create_project_from_job(db_session, user, job.id, output_ratio="16:9", voice_id="voice-a")
    assert (await create_project_from_job(db_session, user, job.id)).id == project.id
    assert project.ecom_set_id is None
    second = await create_project_from_job(db_session, user, job.id, create_new=True, request_id="second-video-request", voice_id="voice-b")
    same = await create_project_from_job(db_session, user, job.id, create_new=True, request_id="second-video-request", voice_id="voice-b")
    assert second.id != project.id and same.id == second.id
    with pytest.raises(ValueError):
        await create_project_from_job(db_session, user, job.id, create_new=True, request_id="second-video-request", voice_id="voice-c")
    with pytest.raises(ValueError):
        await create_project_from_job(db_session, user, job.id, create_new=True, request_id="second-video-request",
                                      voice_id="voice-b", name="其他商品")
    with pytest.raises(LookupError):
        await create_project_from_job(db_session, other, job.id)

    # 使用隔离测试事务，禁止写稿调用上游服务。
    @asynccontextmanager
    async def session():
        yield db_session

    # 上游调用即失败，确保 mock 完全离线。
    def no_network(*args, **kwargs):
        pytest.fail("mock 带货不得联网")

    monkeypatch.setattr("app.database.AsyncSessionLocal", session)
    monkeypatch.setattr("app.services.llm_client.chat_completions", no_network)
    monkeypatch.setattr("app.services.ark.get_ark", no_network)
    assert await project_has_ecom_set(project.id)
    assert await build_daihuo_shots(project.id) == 2
    shots = list((await db_session.scalars(select(Shot).where(Shot.project_id == project.id).order_by(Shot.shot_no))).all())
    assert len(shots) == 2 and all(shot.narration and shot.image_url for shot in shots)
    with Image.open(storage.local_path_from_url(shots[0].image_url)) as frame:
        assert frame.size == (1920, 1080)
    single = await seed_job(db_session, user, tmp_path, count=1)
    with pytest.raises(ValueError):
        await create_project_from_job(db_session, user, single.id)


# 聚合接口对错误查询返回中文短句。
async def test_hub_http_validation(db_session):
    from app.api.ecom_hub import router
    from app.database import get_db
    from app.deps import get_current_user

    user = await make_user(db_session)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # 共用测试事务。
    async def db():
        yield db_session

    # 共用当前测试用户。
    async def current_user():
        await db_session.refresh(user)
        return user

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = current_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tools/ecom/recent")
        assert response.status_code == 200 and response.json()["meta"]["total"] == 0
        for query in ("page=0", "page_size=51", "kind=unknown"):
            response = await client.get(f"/api/tools/ecom/recent?{query}")
            assert response.status_code == 400 and isinstance(response.json()["detail"], str)


# 真实 HTTP multipart 接收来源标识并转换为正确角色，拒绝来源不完整及越权。
@pytest.mark.parametrize("kind,role", [("style", "style"), ("sku", "scene")])
async def test_source_multipart_and_video_http(db_session, monkeypatch, tmp_path, kind, role):
    import io
    import json
    from app.api.ecom_jobs import router as jobs_router
    from app.api.ecom_hub import router as hub_router
    from app.database import get_db
    from app.deps import get_current_user

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    user, other = await make_user(db_session), await make_user(db_session)
    job = await seed_job(db_session, user, tmp_path)
    foreign = await seed_job(db_session, other, tmp_path)
    job_id, foreign_id = job.id, foreign.id
    if not await db_session.get(Template, DAIHUO_TEMPLATE_ID):
        db_session.add(Template(id=DAIHUO_TEMPLATE_ID, name="带货", style_prefix=""))
    await db_session.commit()
    app = FastAPI()
    app.include_router(jobs_router, prefix="/api")
    app.include_router(hub_router, prefix="/api")

    # HTTP 调用复用同一回滚事务。
    async def db():
        yield db_session

    # 当前用户固定为图片所有者。
    async def current_user():
        await db_session.refresh(user)
        return user

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = current_user
    image = io.BytesIO()
    Image.new("RGB", (30, 30), "red").save(image, format="PNG")
    params = {"mode": "single", "count": 1, "ratio": "1:1", "clarity": "2k"} if kind == "style" else {"sku_names": ["红色"]}
    data = {"params": json.dumps(params), "source_job_id": str(job_id), "source_item_idx": "0"}
    files = {"files": ("p.png", image.getvalue(), "image/png")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/tools/ecom-jobs/{kind}", data=data, files=files)
        assert response.status_code == 200, response.text
        output = response.json()
        assert {ref["role"] for ref in output["items"][0]["inputs"]} == {"product", role}
        response = await client.post(f"/api/tools/ecom-jobs/{kind}", data={**data, "source_job_id": str(foreign_id)}, files=files)
        assert response.status_code == 404
        response = await client.post(f"/api/tools/ecom-jobs/{kind}", data={"source_job_id": str(job_id)}, files=files)
        assert response.status_code == 400
        response = await client.post(f"/api/tools/ecom/jobs/{job_id}/project", json={"output_ratio": "3:4"})
        assert response.status_code == 400 and isinstance(response.json()["detail"], str)
        quote = await client.post(f"/api/tools/ecom/jobs/{job_id}/project/quote", json={})
        assert quote.status_code == 200 and quote.json()["image_fen"] == 0
        created = await client.post(f"/api/tools/ecom/jobs/{job_id}/project", json={})
        assert created.status_code == 200, created.text
        linked = await client.get(f"/api/tools/ecom/jobs/{job_id}/project")
        assert linked.json() == created.json()
        forbidden = await client.get(f"/api/tools/ecom/jobs/{foreign_id}/project")
        assert forbidden.status_code == 404


# 陈旧页面或重复点击只能请求写稿，已有分镜时不意外启动配音收费阶段。
async def test_start_script_does_not_advance_existing_storyboard(db_session, monkeypatch, tmp_path):
    from app.models import ProjectStatus
    from app.services.daihuo.images import start_image_project_script
    from app.services.daihuo.job_source import create_project_from_job

    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "project_dir", lambda pid: tmp_path / f"p{pid}")
    user = await make_user(db_session)
    if not await db_session.get(Template, DAIHUO_TEMPLATE_ID):
        db_session.add(Template(id=DAIHUO_TEMPLATE_ID, name="带货", style_prefix=""))
    job = await seed_job(db_session, user, tmp_path)
    project = await create_project_from_job(db_session, user, job.id)
    # 价目 stub 不得掩盖离线缺口；入队和执行预扣都不能访问网络。
    def no_rates(*args, **kwargs):
        pytest.fail("电商 mock 写稿不得查询网络价目")
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", no_rates)
    await start_image_project_script(db_session, user, project.id)
    await start_image_project_script(db_session, user, project.id)
    tasks = list((await db_session.scalars(select(TaskRun).where(TaskRun.project_id == project.id))).all())
    assert len(tasks) == 1 and tasks[0].task_type == "project_pipeline" and tasks[0].payload["phase"] == "script"
    from app.services.billing.settlement import freeze_for_task
    assert await freeze_for_task(db_session, tasks[0]) > 0
    tasks[0].status = "succeeded"
    project.status = ProjectStatus.SCRIPT_READY
    db_session.add(Shot(project_id=project.id, shot_no=1, narration="保温杯", image_url="/static/frame.jpg"))
    await db_session.commit()
    await start_image_project_script(db_session, user, project.id)
    assert await db_session.scalar(select(func.count(TaskRun.id)).where(TaskRun.project_id == project.id)) == 1
