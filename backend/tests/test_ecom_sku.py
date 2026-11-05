"""E4 SKU 替换：边界、画幅、拆分、角色与离线任务。"""
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


# 构造指定尺寸和 EXIF 方向的独立测试图片。
def image_bytes(color, size=(40, 40), orientation=None):
    buffer = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    Image.new("RGB", size, color).save(buffer, format="PNG", exif=exif)
    return buffer.getvalue()


# 默认画幅留给场景推导，备注经合规，SKU 名可自定义。
def test_sku_valid_params():
    clean = kinds.validate("sku", 20, {"notes": "替换左侧全网最好用的杯子"})
    assert clean["ratio"] is None and clean["clarity"] == "2k"
    assert clean["notes"] == "替换左侧好用的杯子"
    assert kinds.validate("sku", 1, {"sku_names": ["红杯"]})["sku_names"] == ["红杯"]


# 数量、枚举、SKU 名与多余参数严格校验。
@pytest.mark.parametrize("count,params", [
    (0, {}), (21, {}), (1, {"ratio": "原比例"}), (1, {"clarity": "8k"}),
    (1, {"notes": "x" * 501}), (1, {"notes": 1}), (1, {"count": 2}),
    (2, {"sku_names": ["红杯"]}), (1, {"sku_names": [""]}), (1, {"sku_names": [" "]}),
    (1, {"sku_names": ["x" * 101]}), (1, {"sku_names": [1]}),
])
def test_sku_invalid_params(count, params):
    with pytest.raises(ValueError):
        kinds.validate("sku", count, params)


# 横竖图与边界均映射到开放画幅。
@pytest.mark.parametrize("size,ratio", [((100, 100), "1:1"), ((750, 1000), "3:4"),
    ((600, 1000), "9:16"), ((1800, 1000), "16:9"), ((400, 1000), "9:16"), ((4000, 1000), "16:9")])
def test_sku_nearest_ratio(size, ratio):
    from app.services.ecom_jobs.sku import nearest_ratio
    assert nearest_ratio(size) == ratio


# 锁住场景、只替换指定商品，保留商品外观并控制比例。
def test_sku_prompt_constraints():
    prompt = kinds.build_item_prompt("sku", {"notes": "替换左侧全网最好用的杯子"}, [{"role": "product"}, {"role": "scene"}], 0)
    for text in ("构图", "背景", "光影", "道具", "机位", "不变", "只替换", "外形", "颜色", "logo", "尺寸比例合理", "左侧", "图1", "图2"):
        assert text in prompt
    assert "全网" not in prompt and "最好用" not in prompt and "借用生活环境" not in prompt


# HTTP 到数据库验证场景必填、上限、逐 SKU 拆分、方向与命名。
@pytest.mark.parametrize("products,params,ratio", [(20, {}, "9:16"), (1, {"ratio": "1:1", "sku_names": ["自定义 SKU"]}, "1:1")])
async def test_sku_create_api(db_session, monkeypatch, tmp_path, products, params, ratio):
    from app.api.ecom_jobs import router
    from app.database import get_db
    from app.deps import get_current_user
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    checked = []
    original = service.ensure_balance_for_task_batch

    # 捕获实际余额校验的输出数量。
    async def check(db, owner, task, total):
        checked.append(total)
        await original(db, owner, task, total)

    # 请求使用测试事务。
    async def db_override():
        yield db_session

    # 刷新并使用当前测试用户。
    async def user_override():
        await db_session.refresh(user)
        return user

    monkeypatch.setattr(service, "ensure_balance_for_task_batch", check)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    files = [("files", (f"杯子{i}.png", image_bytes("red"), "image/png")) for i in range(products)]
    scene = ("scene_files", ("scene.png", image_bytes("navy", (160, 90), 6), "image/png"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for refs in ([], [scene, scene], [scene, ("model_files", ("m.png", image_bytes("white"), "image/png"))]):
            assert (await client.post("/api/tools/ecom-jobs/sku", files=files + refs)).status_code == 400
        assert (await client.post("/api/tools/ecom-jobs/sku", files=[files[0]] * 21 + [scene])).status_code == 400
        response = await client.post("/api/tools/ecom-jobs/sku", files=files + [scene], data={"params": json.dumps(params)})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == products and checked == [products, products]
        assert body["params"]["ratio"] == ratio
        assert body["params"]["sku_names"] == params.get("sku_names", [f"杯子{i}" for i in range(products)])
        urls = []
        for item in body["items"]:
            assert item["ratio"] == ratio
            assert [ref["role"] for ref in item["inputs"]] == ["product", "scene"]
            assert all("path" not in ref for ref in item["inputs"])
            urls.append(item["inputs"][0]["url"])
            task = await db_session.get(TaskRun, item["task_run_id"])
            assert task.task_type == "ecom_job_image" and task.payload["kind"] == "sku"
        assert len(set(urls)) == products
        spec = (await client.get("/api/tools/ecom-jobs/options")).json()["kinds"]["sku"]
        assert spec["max_images"] == 20 and set(spec["ratios"]) == {"1:1", "3:4", "9:16", "16:9"}


# 完整 mock handler 只读本地图，验证每个 SKU 输出且不调用网络边界。
async def test_sku_mock_handler(db_session, monkeypatch, tmp_path):
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)

    # 任何模型、OSS、价目、用量边界调用都失败。
    def forbid_network(*args, **kwargs):
        raise AssertionError("mock 不得联网")

    # handler 共用测试事务。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr("app.services.ark.get_ark", forbid_network)
    monkeypatch.setattr(storage, "publish_local", forbid_network)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", forbid_network)
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", forbid_network)
    job = await service.create_job(db_session, user, kind="sku", params={},
        uploads=[(image_bytes("red"), "red.png"), (image_bytes("lime"), "green.png")],
        scene_uploads=[(image_bytes("navy", (160, 90)), "scene.png")])
    tasks = []
    for item in job.items:
        item.size = "160x90"
        tasks.append(await db_session.get(TaskRun, item.task_run_id))
    await db_session.commit()
    for task in tasks:
        result = await jobs.run_item_job(task)
        assert result["ok"]
        with Image.open(storage.local_path_from_url(result["url"])) as image:
            assert image.format == "PNG" and image.size == (160, 90)
            assert image.getpixel((0, 0))[:3] == (0, 0, 128)
            assert image.getpixel((80, 45))[:3] == ((255, 0, 0) if task.payload["idx"] == 0 else (0, 255, 0))
    assert (await service.get_job(db_session, user, job.id)).status == "done"


# 真实生成边界按提示词顺序传递产品与场景，启用结构化引用。
async def test_sku_provider_roles(monkeypatch, tmp_path):
    from app.services import ark, storage

    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    captured = {}

    # 拦截模型边界检查参数。
    async def gen_image(prompt, negative, refs, **kwargs):
        captured.update(refs=refs, **kwargs)
        return ark.ImageResult(local_url="/static/out.png")

    # 不下载测试成图。
    async def ensure_local(url, dest):
        return dest

    monkeypatch.setattr(ark, "get_ark", lambda: SimpleNamespace(gen_image=gen_image))
    monkeypatch.setattr(storage, "ensure_local_media", ensure_local)
    inputs = [{"role": role, "url": f"https://example.com/{role}.png"} for role in ("product", "scene")]
    await jobs.generate_image(None, SimpleNamespace(user_id=1, id=1, kind="sku", params={}),
        SimpleNamespace(idx=0, prompt="替换", ratio="1:1", size="64x64", inputs=inputs))
    assert captured["reference_roles"] == ["product", "scene"]
    assert captured["refs"] == [ref["url"] for ref in inputs] and captured["structured_refs"] is True


# SKU 复用通用 handler 和两档图片目录估价。
@pytest.mark.parametrize("clarity,price", [("2k", 23), ("4k", 47)])
async def test_sku_handler_and_estimate(monkeypatch, clarity, price):
    from app.services.billing import estimates
    from app.services.tasks.handlers import get_task_handler

    # 使用确定目录价格验证按清晰度计价。
    def catalog(settings, *, size, payload):
        assert size == clarity and payload["kind"] == "sku"
        return price

    monkeypatch.setattr(estimates, "_catalog_image_fen", catalog)
    task = TaskRun(domain="studio", task_type="ecom_job_image", payload={"kind": "sku", "clarity": clarity})
    assert callable(get_task_handler("studio", "ecom_job_image").executor)
    assert await estimates.estimate_task_fen(None, task) == price


# 共用裂变接口从多 SKU 任务选中单项时，只延续该 SKU 名并覆盖全部派生结果。
async def test_sku_variants_preserve_selected_name(db_session, monkeypatch, tmp_path):
    from app.services import storage
    from app.services.ecom_jobs.variants import create_variants
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    job = await service.create_job(db_session, user, kind="sku", params={"sku_names": ["红杯", "绿杯"]},
        uploads=[(image_bytes("red"), "r.png"), (image_bytes("lime"), "g.png")],
        scene_uploads=[(image_bytes("navy"), "s.png")])
    dest = jobs.job_dir(user.id, job.id) / "result_1_test.png"
    dest.write_bytes(image_bytes("lime"))
    job.items[1].status = "succeeded"
    job.items[1].result_url = storage.rel_static_url(dest)
    await db_session.commit()
    variant = await create_variants(db_session, user, job.id, 1, count=3)
    assert variant.params["sku_names"] == ["绿杯"] * 3
    assert len(variant.items) == 3
    for item in variant.items:
        assert [ref["role"] for ref in item.inputs] == ["product", "scene", "source"]
        assert "裂变拍摄变化" not in item.prompt
