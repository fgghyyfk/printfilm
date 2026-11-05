"""E1 风格复刻：输入边界、提示词、任务拆分、角色传递与离线出图。"""
from contextlib import asynccontextmanager
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app.config import get_settings
from app.models_tasks import TaskRun
from app.services.ecom_jobs import jobs, kinds, service


# 构造可识别的测试图片，避免依赖仓库媒体素材。
def image_bytes(color):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(buffer, format="PNG")
    return buffer.getvalue()


# 两种模式均接受合法边界，并对补充要求做合规替换。
@pytest.mark.parametrize("mode,products,count", [("single", 1, 1), ("single", 6, 4), ("batch", 1, 1), ("batch", 20, 1)])
def test_style_valid_boundaries(mode, products, count):
    clean = kinds.validate("style", products, {"mode": mode, "count": count, "notes": "全网最好用的杯子"})
    assert clean["notes"] == "好用的杯子"
    assert clean["mode"] == mode and clean["count"] == count


# 拒绝模式数量越界、无效枚举、非整数数量和额外参数。
@pytest.mark.parametrize("products,params", [
    (0, {}), (7, {"mode": "single"}), (21, {"mode": "batch"}),
    (1, {"count": 0}), (1, {"count": 5}), (1, {"count": True}), (1, {"count": "2"}),
    (1, {"mode": "batch", "count": 2}), (1, {"mode": "other"}),
    (1, {"ratio": "原比例"}), (1, {"clarity": "8k"}), (1, {"notes": "x" * 501}),
    (1, {"notes": 1}), (1, {"mode": []}), (1, {"background": "white"}),
])
def test_style_invalid_params(products, params):
    with pytest.raises(ValueError):
        kinds.validate("style", products, params)


# 风格参考只用于设计，主体保真且禁止复制参考设计里的商标文字。
def test_style_prompt_constraints():
    params = kinds.validate("style", 6, {"notes": "全网最好用的杯子"})
    prompt = kinds.KINDS["style"].prompt_builder(params)
    for text in ("构图", "光影", "配色", "版式氛围", "外形", "颜色", "logo", "文字不变", "不要复制参考图里的品牌、logo与文字", "所有产品图"):
        assert text in prompt
    assert "全网" not in prompt and "最好用" not in prompt


# API 必须按角色存储输入，以实际输出数量校验余额并建立逐张任务。
@pytest.mark.parametrize("mode,products,count,outputs", [("single", 6, 4, 4), ("batch", 20, 1, 20)])
async def test_style_create_api(db_session, monkeypatch, tmp_path, mode, products, count, outputs):
    from app.api.ecom_jobs import router
    from app.database import get_db
    from app.deps import get_current_user
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    checked_counts = []
    original_check = service.ensure_balance_for_task_batch

    # 捕获前置余额校验的计数，同时保留真实的余额规则。
    async def check(db, owner, task, total):
        checked_counts.append(total)
        await original_check(db, owner, task, total)

    # 接口与服务共用回滚事务，不启动调度器。
    async def db_override():
        yield db_session

    # 将当前请求绑定到测试用户。
    async def user_override():
        await db_session.refresh(user)
        return user

    monkeypatch.setattr(service, "ensure_balance_for_task_batch", check)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    files = [("style_files", ("design.png", image_bytes("navy"), "image/png"))]
    files += [("files", (f"product{i}.png", image_bytes("red"), "image/png")) for i in range(products)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for refs in ([], files[:1] * 2):
            invalid = await client.post("/api/tools/ecom-jobs/style", files=refs + files[1:], data={"params": json.dumps({"mode": mode, "count": count})})
            assert invalid.status_code == 400
        response = await client.post("/api/tools/ecom-jobs/style", files=files, data={"params": json.dumps({"mode": mode, "count": count, "ratio": "16:9"})})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == outputs
        assert checked_counts == [outputs, outputs]
        product_urls = []
        for item in body["items"]:
            assert item["ratio"] == "16:9"
            assert [ref["role"] for ref in item["inputs"]] == ["product"] * (products if mode == "single" else 1) + ["style"]
            assert all("path" not in ref for ref in item["inputs"])
            task = await db_session.get(TaskRun, item["task_run_id"])
            assert task.task_type == "ecom_job_image" and task.payload["kind"] == "style"
            product_urls.append(item["inputs"][0]["url"])
        if mode == "batch":
            assert len(set(product_urls)) == products
        options = (await client.get("/api/tools/ecom-jobs/options")).json()["kinds"]["style"]
        assert options["mode_limits"] == {"single": 6, "batch": 20}
        assert options["max_count"] == 4
        assert set(options["ratios"]) == {"1:1", "3:4", "9:16", "16:9"}


# 风格 mock 完整跑过 handler，参考主色作底，多个商品居中可见，绝不联网。
async def test_style_mock_handler(db_session, monkeypatch, tmp_path):
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)

    # 如果错误进入付费调用、上传、价目或计费路径，立即失败。
    def forbid_network(*args, **kwargs):
        raise AssertionError("mock 不得联网")

    # handler 复用回滚事务，完成后不留业务记录。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr("app.services.ark.get_ark", forbid_network)
    monkeypatch.setattr(storage, "publish_local", forbid_network)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", forbid_network)
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", forbid_network)
    job = await service.create_job(db_session, user, kind="style", params={},
        uploads=[(image_bytes("red"), "p1.png"), (image_bytes("lime"), "p2.png")],
        style_uploads=[(image_bytes("navy"), "ref.png")])
    item = job.items[0]
    item.size = "120x80"
    await db_session.commit()
    task = await db_session.get(TaskRun, item.task_run_id)
    result = await jobs.run_item_job(task)
    job = await service.get_job(db_session, user, job.id)
    assert result["ok"] and job.status == "done"
    with Image.open(storage.local_path_from_url(result["url"])) as image:
        assert image.format == "PNG" and image.size == (120, 80)
        assert image.getpixel((0, 0))[:3] == (0, 0, 128)
        assert image.getpixel((35, 40))[:3] == (255, 0, 0)
        assert image.getpixel((85, 40))[:3] == (0, 255, 0)


# 真正的生成分支按 role 分流引用，显式开启结构化图片输入。
async def test_style_provider_roles(monkeypatch, tmp_path):
    from app.services import storage
    from app.services.ark import ImageResult

    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    called = {}

    # 仅截住外部模型，观察实际调用的参数。
    async def gen_image(prompt, negative, refs, **kwargs):
        called.update(refs=refs, **kwargs)
        return ImageResult(local_url="/static/out.png")

    # 保留图片接口的本地落盘调用契约。
    async def ensure_local(url, dest):
        return dest

    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(gen_image=gen_image))
    monkeypatch.setattr(storage, "ensure_local_media", ensure_local)
    job = SimpleNamespace(user_id=1, id=1, kind="style", params={})
    item = SimpleNamespace(idx=0, prompt="风格复刻", ratio="1:1", size="64x64", inputs=[
        {"role": "style", "url": "https://example.com/style.png"},
        {"role": "product", "url": "https://example.com/product.png"},
    ])
    await jobs.generate_image(None, job, item)
    assert called["refs"] == ["https://example.com/product.png"]
    assert called["style_ref_urls"] == ["https://example.com/style.png"]
    assert called["structured_refs"] is True


# E1 复用通用图任务登记及两档目录估价，不增加第二套计费链路。
@pytest.mark.parametrize("clarity,price", [("2k", 23), ("4k", 47)])
async def test_style_handler_and_estimate(monkeypatch, clarity, price):
    from app.services.billing import estimates
    from app.services.tasks.handlers import get_task_handler

    # 使用确定性目录价格，验证 kind 不会落入文本估价。
    def catalog(settings, *, size, payload):
        assert size == clarity and payload["kind"] == "style"
        return price

    monkeypatch.setattr(estimates, "_catalog_image_fen", catalog)
    task = TaskRun(domain="studio", task_type="ecom_job_image", payload={"kind": "style", "clarity": clarity})
    assert callable(get_task_handler("studio", "ecom_job_image").executor)
    assert await estimates.estimate_task_fen(None, task) == price


# 六张商品与一张设计参考都必须穿过实际网关，不得被通用引用上限截断。
@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5"])
async def test_style_gateway_preserves_all_refs(monkeypatch, model):
    from app.services import ark

    gateway = ark.ArkGateway(get_settings().model_copy(update={"model_image": model}))
    monkeypatch.setattr(gateway, "_resolve_ark_route", lambda *args: None)
    monkeypatch.setattr(gateway, "_route_headers", lambda *args: {})
    products = [f"https://example.com/p{i}.png" for i in range(6)]
    reference = "https://example.com/style.png"
    captured = {}

    # 在真实请求体构造之后拦截 HTTP，只验证协议，不请求付费模型。
    async def capture(client, method, url, **kwargs):
        captured.update(kwargs["json"])
        raise RuntimeError("request captured")

    monkeypatch.setattr(ark, "observed_request", capture)
    with pytest.raises(RuntimeError, match="request captured"):
        await gateway._seedream_once("复刻参考图构图", products, project_id=0, shot_no=1, size="2K",
                                     style_ref_urls=[reference], structured_refs=True)
    if model == "gpt-image-2.5":
        assert all(url in captured["prompt"] for url in products + [reference])
        assert "禁止抄主体与构图" not in captured["prompt"]
    else:
        urls = [block["image_url"] for block in captured["input"][0]["content"] if block["type"] == "input_image"]
        assert urls == products + [reference]


# 文本降级仍需遵循正文的构图复刻要求，不能追加相反的通用限制。
def test_style_fallback_keeps_composition_contract():
    from app.services.tokenfree_image import build_tokenfree_openai_image_body

    body = build_tokenfree_openai_image_body(model="gpt-image-2.5", prompt="复刻构图", structured_refs=True,
        ref_urls=["https://example.com/product.png"], style_ref_urls=["https://example.com/style.png"])
    assert "禁止抄主体与构图" not in body["prompt"]
    assert "复刻构图" in body["prompt"]
