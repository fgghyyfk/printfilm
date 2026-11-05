"""电商批量任务：参数、提示词、透明底、mock 与入库集成。"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.services.ecom_jobs import kinds, refine


# 参数必须匹配已注册 kind，数量和枚举严格校验。
@pytest.mark.parametrize("kind,count,params", [
    ("unknown", 1, {}), ("refine", 0, {}), ("refine", 21, {}),
    ("refine", 1, {"ratio": "16:9"}), ("refine", 1, {"clarity": "8k"}),
    ("refine", 1, {"background": "blue"}),
])
def test_kind_validation(kind, count, params):
    with pytest.raises(ValueError):
        kinds.validate(kind, count, params)


# 精修提示词保留外观约束，透明底先生成白底，备注过滤违规词。
def test_refine_prompt():
    params = kinds.validate("refine", 1, {"background": "transparent", "notes": "全网最好用的杯子"})
    prompt = refine.build_prompt(params)
    assert "纯白" in prompt and "logo" in prompt and "完全一致" in prompt
    assert "全网" not in prompt and "最好用" not in prompt
    assert params["notes"] == "好用的杯子"


# 泛洪只移除四边连通白色，保留主体中封闭的白色图案。
def test_remove_white_background():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 24, 24), fill="navy")
    draw.rectangle((10, 10, 15, 15), fill="white")
    result = refine.remove_white_background(image)
    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((5, 5))[3] == 255
    assert result.getpixel((12, 12))[3] == 255


# 本地 mock 生成目标尺寸 PNG，完全不需要模型或网络。
def test_mock_image(tmp_path):
    from app.services.ecom_jobs.mock import generate_mock
    src, dest = tmp_path / "input.png", tmp_path / "output.png"
    Image.new("RGB", (80, 120), "navy").save(src)
    result = generate_mock(src, dest, size="1536x2048", background="white")
    with Image.open(result) as image:
        assert image.size == (1536, 2048)
        assert image.format == "PNG"


# 建任务接口必须真实写入 job、items 和逐张 TaskRun，并按用户隔离。
async def test_create_job_api(db_session, monkeypatch, tmp_path):
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import select
    from app.api.ecom_jobs import router
    from app.database import get_db
    from app.deps import get_current_user
    from app.models_ecom import EcomJob, EcomJobItem
    from app.models_tasks import TaskRun
    from app.services import storage
    from app.config import get_settings
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "publish_local", lambda path, **_: "/static/" + path.name)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # 使用事务回滚 fixture 提供认证与数据库，不启动真实调度器。
    async def db_override():
        yield db_session

    # 当前测试用户拥有全部新建记录。
    async def user_override():
        await db_session.refresh(user)
        return user

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    data = io.BytesIO()
    Image.new("RGB", (120, 80), "red").save(data, format="PNG")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/tools/ecom-jobs/refine", files=[
            ("files", ("product.png", data.getvalue(), "image/png")),
            ("files", ("product2.png", data.getvalue(), "image/png")),
        ], data={"params": '{"ratio":"原比例","clarity":"2k"}'})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "queued" and len(body["items"]) == 2
        assert all("path" not in ref for item in body["items"] for ref in item["inputs"])
        job = await db_session.get(EcomJob, body["id"])
        assert job.user_id == user.id
        items = (await db_session.execute(select(EcomJobItem).where(EcomJobItem.job_id == job.id))).scalars().all()
        assert len(items) == 2 and all(item.task_run_id for item in items)
        assert items[0].ratio == "3:2"
        task = await db_session.get(TaskRun, items[0].task_run_id)
        assert task.task_type == "ecom_job_image"
        retry = await client.post(f'/api/tools/ecom-jobs/{job.id}/items/0/retry')
        assert retry.status_code == 400
        page = await client.get("/api/tools/ecom-jobs?page_size=1&kind=refine")
        assert page.json()["meta"]["total"] == 1
        invalid_page = await client.get("/api/tools/ecom-jobs?page=bad")
        missing_file = await client.post("/api/tools/ecom-jobs/refine")
        for invalid in (invalid_page, missing_file):
            assert invalid.status_code == 400 and "参数错误" in invalid.json()["detail"]
        user = await make_user(db_session)
        await db_session.commit()
        other_detail = await client.get(f'/api/tools/ecom-jobs/{body["id"]}')
        other_retry = await client.post(f'/api/tools/ecom-jobs/{body["id"]}/items/0/retry')
        other_download = await client.get(f'/api/tools/ecom-jobs/{body["id"]}/download')
        assert all(response.status_code == 404 for response in (other_detail, other_retry, other_download))


# 错误类型的枚举和上传边界应稳定返回校验失败，不能变成服务器错误。
@pytest.mark.parametrize("params", [{"background": []}, {"background": {}}, {"ratio": []}, {"notes": 12}])
def test_invalid_param_types(params):
    with pytest.raises(ValueError):
        kinds.validate("refine", 1, params)


# 过长窄图和损坏图片在保存前拒绝，避免产生上游不支持的尺寸。
def test_upload_validation():
    from app.services.ecom_jobs.service import inspect_upload
    data = io.BytesIO()
    Image.new("RGB", (1000, 10), "white").save(data, format="PNG")
    with pytest.raises(ValueError, match="尺寸"):
        inspect_upload(data.getvalue(), "long.png")
    with pytest.raises(ValueError, match="无法识别"):
        inspect_upload(b"broken", "broken.png")


# mock handler 可完成透明精修、汇总状态、ZIP，且重复执行不会再次生图。
async def test_mock_handler_and_zip(db_session, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from app.config import get_settings
    from app.models_tasks import TaskRun
    from app.services import storage
    from app.services.ecom_jobs import jobs, service
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)

    # 如 mock 意外进入真实模型/上传/记账路径，测试立即失败。
    def forbid_network(*args, **kwargs):
        raise AssertionError("mock 不得访问外部服务")

    # 使用当前测试事务执行 handler，保证其内部 commit 仍在测试外层回滚内。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr("app.services.ark.get_ark", forbid_network)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", forbid_network)
    monkeypatch.setattr(storage, "publish_local", forbid_network)
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", forbid_network)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="PNG")
    job = await service.create_job(db_session, user, kind="refine", uploads=[(data.getvalue(), "p.png")],
                                   params={"background": "transparent"})
    item = job.items[0]
    item.size = "64x64"
    await db_session.commit()
    task = await db_session.get(TaskRun, item.task_run_id)
    result = await jobs.run_item_job(task)
    assert result["ok"] is True
    job = await service.get_job(db_session, user, job.id)
    assert job.status == "done" and job.items[0].status == "succeeded"
    with Image.open(storage.local_path_from_url(result["url"])) as image:
        assert image.size == (64, 64) and image.getpixel((0, 0))[3] == 0
        assert image.getpixel((32, 32))[3] == 255
    packed, name = await service.build_zip(db_session, user, job.id)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.namelist() == ["01.png"]
    assert name.endswith(".zip")
    assert (await jobs.run_item_job(task))["skipped"] == "superseded"


# 生成异常必须落失败状态并重抛；终止后的单张重试创建新的 TaskRun。
async def test_handler_failure_and_retry(db_session, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from app.config import get_settings
    from app.models_tasks import TaskRun
    from app.services import storage
    from app.services.ecom_jobs import jobs, service
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)

    # 把生成阶段故障注入到真实 handler 状态收尾之前。
    async def failed_image(*args):
        raise RuntimeError("private provider failure")

    # handler 与 service 共用测试事务，避免访问业务库。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr(jobs, "generate_image", failed_image)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="PNG")
    job = await service.create_job(db_session, user, kind="refine", uploads=[(data.getvalue(), "p.png")], params={})
    job_id, old_id = job.id, job.items[0].task_run_id
    task = await db_session.get(TaskRun, old_id)
    with pytest.raises(RuntimeError, match="private provider failure"):
        await jobs.run_item_job(task)
    await db_session.refresh(user)
    job = await service.get_job(db_session, user, job_id)
    assert job.status == "failed" and job.items[0].status == "failed"
    assert "private provider" not in job.items[0].error
    task = await db_session.get(TaskRun, old_id)
    task.status = "failed"
    await db_session.commit()
    retried = await service.retry_item(db_session, user, job_id, 0)
    assert retried.status == "queued" and retried.items[0].task_run_id != old_id


# 整批余额不足时不创建任何 job、item、TaskRun 或上传文件。
async def test_batch_insufficient_balance(db_session, monkeypatch, tmp_path):
    from sqlalchemy import func, select
    from app.models_ecom import EcomJob
    from app.services import storage
    from app.services.ecom_jobs import service
    from app.services.public_errors import InsufficientBalanceError
    from conftest import make_user

    user = await make_user(db_session, balance_fen=0)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="PNG")
    with pytest.raises(InsufficientBalanceError):
        await service.create_job(db_session, user, kind="refine", uploads=[(data.getvalue(), "p.png")], params={})
    assert (await db_session.execute(select(func.count()).select_from(EcomJob).where(EcomJob.user_id == user.id))).scalar_one() == 0
    assert not list(tmp_path.rglob("*.png"))


# 真实 handler 分支传递结构化参考图标记，并将模型用量登记到本张 TaskRun。
async def test_handler_provider_usage(db_session, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from sqlalchemy import select
    from app.config import get_settings
    from app.models import UsageEvent
    from app.models_tasks import TaskRun
    from app.services import storage
    from app.services.ark import ImageResult
    from app.services.billing.context import billing_scope
    from app.services.ecom_jobs import jobs, service
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    monkeypatch.setattr(storage, "publish_local", lambda path, **_: storage.rel_static_url(path))
    provider = tmp_path / "provider.png"
    Image.new("RGB", (64, 64), "red").save(provider)
    called = {}

    # 仅替换付费模型调用，保留生成落盘、用量记录与状态更新的真实实现。
    async def fake_image(prompt, negative, refs, **kwargs):
        called.update({"refs": refs, **kwargs})
        return ImageResult(local_url=storage.rel_static_url(provider), total_tokens=100, upstream_cost_fen=13)

    # 通过测试事务运行 handler，避免连接业务数据库。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(gen_image=fake_image))
    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="PNG")
    job = await service.create_job(db_session, user, kind="refine", uploads=[(data.getvalue(), "p.png")], params={})
    task = await db_session.get(TaskRun, job.items[0].task_run_id)
    async with billing_scope(task.id):
        await jobs.run_item_job(task)
    assert called["structured_refs"] is True and called["aspect_ratio"] == "1:1"
    assert called["refs"][0] == storage.to_public_url(job.items[0].inputs[0]["url"])
    event = (await db_session.execute(select(UsageEvent).where(UsageEvent.task_run_id == task.id))).scalar_one()
    import json
    assert event.domain == "studio" and json.loads(event.raw_usage_json)["tool_id"] == "ecom_refine"


# options 价格探针与实际任务估价保持一致，接口不暴露未实现的 kind。
async def test_options_prices(db_session):
    from app.services.ecom_jobs import service
    from conftest import make_user
    user = await make_user(db_session)
    options = await service.get_options(db_session, user)
    assert set(options["kinds"]) == {"refine"}
    assert options["kinds"]["refine"]["max_images"] == 20
    for clarity in ("2k", "4k"):
        assert options["kinds"]["refine"]["prices"][clarity] == await service.image_unit_fen(db_session, clarity)


# 调度器终止任务时详情和历史都收敛图片状态；混合成功失败聚合为 partial。
async def test_reconcile_and_partial_status(db_session, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.models_tasks import TaskRun
    from app.services import storage
    from app.services.ecom_jobs import jobs, service
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="PNG")
    job = await service.create_job(db_session, user, kind="refine", params={}, uploads=[
        (data.getvalue(), "one.png"), (data.getvalue(), "two.png"),
    ])
    task = await db_session.get(TaskRun, job.items[0].task_run_id)
    task.status = "failed"
    await db_session.commit()
    job = await service.get_job(db_session, user, job.id)
    assert job.items[0].status == "failed" and job.status == "queued"
    second_task = await db_session.get(TaskRun, job.items[1].task_run_id)
    second_task.status = "cancelled"
    await db_session.commit()
    rows, total = await service.list_jobs(db_session, user, page=1, page_size=12, kind="refine")
    assert total == 1 and rows[0].status == "failed"
    assert rows[0].items[1].error == "任务已取消"
    rows[0].items[1].status = "succeeded"
    assert await jobs.refresh_job_status(db_session, job.id) == "partial"
