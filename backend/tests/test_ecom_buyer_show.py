"""E3 买家秀：参数、角色、多样性、裂变、离线出图与计费契约。"""
from contextlib import asynccontextmanager
import io
import json

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services.ecom_jobs import jobs, kinds, service


# 构造小图片，测试不依赖生成素材或网络。
def image_bytes(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 50), color).save(buffer, format="PNG")
    return buffer.getvalue()


# 默认竖图，合法边界和备注合规必须同时生效。
@pytest.mark.parametrize("mode,products,count", [("buyer", 1, 1), ("seeding", 9, 8)])
def test_buyer_valid(mode, products, count):
    clean = kinds.validate("buyer_show", products, {"mode": mode, "count": count, "notes": "全网最好用的杯子"})
    assert clean["ratio"] == "3:4" and clean["clarity"] == "2k"
    assert clean["notes"] == "好用的杯子"


# 输入张数、严格整数、模式、画幅和额外参数均不得越界。
@pytest.mark.parametrize("products,params", [
    (0, {}), (10, {}), (1, {"count": 0}), (1, {"count": 9}), (1, {"count": True}),
    (1, {"count": "2"}), (1, {"mode": "single"}), (1, {"ratio": "原比例"}),
    (1, {"clarity": "8k"}), (1, {"notes": "x" * 501}), (1, {"notes": 1}), (1, {"extra": 1}),
])
def test_buyer_invalid(products, params):
    with pytest.raises(ValueError):
        kinds.validate("buyer_show", products, params)


# 八张提示词必须有真实角度变化；无可选图时不能要求不存在的参考。
@pytest.mark.parametrize("mode", ["buyer", "seeding"])
def test_buyer_prompts(mode):
    clean = kinds.validate("buyer_show", 1, {"mode": mode, "count": 8})
    prompts = [kinds.build_item_prompt("buyer_show", clean, [{"role": "product"}], idx) for idx in range(8)]
    assert len(set(prompts)) == 8
    assert all("不展示人物面部" in prompt and "自然生活场景" in prompt for prompt in prompts)
    assert all("图2" not in prompt for prompt in prompts)
    assert "手机随手拍" in prompts[0] if mode == "buyer" else "小红书" in prompts[0]
    prompt = kinds.build_item_prompt("buyer_show", clean, [{"role": role} for role in ("product", "model", "scene", "source")], 0)
    for text in ("图2", "外形参考", "不复制真人身份", "名人", "图3", "图4", "logo"):
        assert text in prompt


# 使用真实 API 和事务验证上传角色、逐张任务、裂变来源及用户隔离。
async def test_buyer_api_and_variants(db_session, monkeypatch, tmp_path):
    from app.api.ecom_jobs import router
    from app.database import get_db
    from app.deps import get_current_user
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    stranger = await make_user(db_session)
    owner = user
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    checked = []
    original_check = service.ensure_balance_for_task_batch

    # 记录实际整批校验数量，沿用真实余额规则。
    async def check(db, user, task, count):
        checked.append(count)
        await original_check(db, user, task, count)

    # 共享回滚事务，不启动后台调度器。
    async def db_override():
        yield db_session

    # 动态切换认证用户，验证他人任务不可派生。
    async def user_override():
        await db_session.refresh(owner)
        return owner

    monkeypatch.setattr(service, "ensure_balance_for_task_batch", check)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    files = [("files", (f"p{i}.png", image_bytes(), "image/png")) for i in range(9)]
    files += [("model_files", ("m.png", image_bytes("blue"), "image/png")), ("scene_files", ("s.png", image_bytes("green"), "image/png"))]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for invalid in (files + [files[-1]], files + [files[-2]], files + [("style_files", files[-1][1])]):
            response = await client.post("/api/tools/ecom-jobs/buyer_show", files=invalid)
            assert response.status_code == 400
        response = await client.post("/api/tools/ecom-jobs/buyer_show", files=files, data={"params": json.dumps({"count": 8})})
        assert response.status_code == 200, response.text
        body = response.json()
        assert checked == [8, 8] and len(body["items"]) == 8
        assert len({item["prompt"] for item in body["items"]}) == 8
        assert [ref["role"] for ref in body["items"][0]["inputs"]] == ["product"] * 9 + ["model", "scene"]
        assert all("path" not in ref for ref in body["items"][0]["inputs"])
        url = f'/api/tools/ecom-jobs/{body["id"]}/items/0/variants'
        assert (await client.post(url, json={"count": 3})).status_code == 400
        await db_session.refresh(user)
        job = await service.get_job(db_session, user, body["id"])
        source = jobs.job_dir(user.id, job.id) / "result_0_test.png"
        Image.new("RGB", (40, 50), "navy").save(source)
        job.items[0].status = "succeeded"
        job.items[0].result_url = storage.rel_static_url(source)
        await db_session.commit()
        for count in (0, 9, True, "2"):
            assert (await client.post(url, json={"count": count})).status_code == 400
        assert (await client.post(url, json={"count": 2, "extra": 1})).status_code == 400
        assert (await client.post(url.replace("/items/0", "/items/99"), json={"count": 2})).status_code == 404
        owner = stranger
        assert (await client.post(url, json={"count": 2})).status_code == 404
        owner = user
        derived = await client.post(url, json={"count": 3})
        assert derived.status_code == 200, derived.text
        child = derived.json()
        assert child["id"] != body["id"] and child["kind"] == "buyer_show"
        assert child["params"]["count"] == 3 and len(child["items"]) == 3
        assert len({item["prompt"] for item in child["items"]}) == 3
        assert checked[-2:] == [3, 3]
        assert child["items"][0]["inputs"][-1]["role"] == "source"
        child_job = await service.get_job(db_session, user, child["id"])
        assert all(str(jobs.job_dir(user.id, child_job.id)) in ref["path"] for ref in child_job.items[0].inputs)
        with Image.open(child_job.items[0].inputs[-1]["path"]) as image:
            assert image.getpixel((0, 0))[:3] == (0, 0, 128)
        for item in child_job.items:
            task = await db_session.get(TaskRun, item.task_run_id)
            assert task.payload["kind"] == "buyer_show" and task.task_type == "ecom_job_image"
        options = (await client.get("/api/tools/ecom-jobs/options")).json()["kinds"]["buyer_show"]
        assert options["max_count"] == 8 and options["max_images"] == 9


# 不依赖新增 kind 的专属代码，旧精修也可用完成结果派生多张。
async def test_variants_generic(db_session, monkeypatch, tmp_path):
    from app.services.ecom_jobs.variants import create_variants
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    job = await service.create_job(db_session, user, kind="refine", uploads=[(image_bytes(), "p.png")], params={})
    source = jobs.job_dir(user.id, job.id) / "result_0_test.png"
    Image.new("RGB", (40, 50), "navy").save(source)
    job.items[0].status, job.items[0].result_url = "succeeded", storage.rel_static_url(source)
    await db_session.commit()
    child = await create_variants(db_session, user, job.id, 0, count=2)
    assert child.kind == "refine" and len(child.items) == 2
    assert len({item.prompt for item in child.items}) == 2


# 通用裂变不能把输出张数写成风格参数不接受的值，派生结果仍应能再次裂变。
@pytest.mark.parametrize("mode", ["single", "batch"])
async def test_style_variants_repeat(db_session, monkeypatch, tmp_path, mode):
    from app.services.ecom_jobs.variants import create_variants
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    job = await service.create_job(db_session, user, kind="style", params={"mode": mode},
                                   uploads=[(image_bytes(), "p.png")], style_uploads=[(image_bytes("navy"), "s.png")])
    for count in (8, 2):
        source = jobs.job_dir(user.id, job.id) / "result_0_test.png"
        Image.new("RGB", (40, 50), "navy").save(source)
        job.items[0].status, job.items[0].result_url = "succeeded", storage.rel_static_url(source)
        await db_session.commit()
        job = await create_variants(db_session, user, job.id, 0, count=count)
        assert len(job.items) == count and job.kind == "style"
        assert job.params["count"] == 1
        assert [ref["role"] for ref in job.items[0].inputs] == ["product", "style", "source"]


# 完整 handler 的 mock 在两种模式下都能出图，不调用模型、上传或计费用量。
@pytest.mark.parametrize("mode", ["buyer", "seeding"])
async def test_buyer_mock_handler(db_session, monkeypatch, tmp_path, mode):
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)

    # 任意误入网络或真实收费都应使离线测试失败。
    def forbid(*args, **kwargs):
        raise AssertionError("mock 不得联网")

    # handler 使用当前回滚事务。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr("app.services.ark.get_ark", forbid)
    monkeypatch.setattr(storage, "publish_local", forbid)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", forbid)
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", forbid)
    job = await service.create_job(db_session, user, kind="buyer_show", uploads=[(image_bytes(), "p.png")], params={"mode": mode, "count": 2})
    for item in job.items:
        item.size = "90x120"
    await db_session.commit()
    outputs = []
    for item in job.items:
        result = await jobs.run_item_job(await db_session.get(TaskRun, item.task_run_id))
        with Image.open(storage.local_path_from_url(result["url"])) as image:
            assert image.format == "PNG" and image.size == (90, 120)
            outputs.append(image.tobytes())
    assert outputs[0] != outputs[1]
    assert (await service.get_job(db_session, user, job.id)).status == "done"


# 共用任务登记和两档估价，不能为买家秀另建计费链路。
@pytest.mark.parametrize("clarity,price", [("2k", 23), ("4k", 47)])
async def test_buyer_estimate(monkeypatch, clarity, price):
    from app.services.billing import estimates
    from app.services.tasks.handlers import get_task_handler

    # 确定性价目验证 clarity 正确传到目录。
    def catalog(settings, *, size, payload):
        assert size == clarity and payload["kind"] == "buyer_show"
        return price

    monkeypatch.setattr(estimates, "_catalog_image_fen", catalog)
    assert callable(get_task_handler("studio", "ecom_job_image").executor)
    task = TaskRun(domain="studio", task_type="ecom_job_image", payload={"kind": "buyer_show", "clarity": clarity})
    assert await estimates.estimate_task_fen(None, task) == price
