"""E2：自定义槽位、多语言、广告画幅与清晰度。"""
import pytest
from PIL import Image
from sqlalchemy import text

from app.models_tasks import TaskRun
from app.services.ecom_set import presets, prompts, planner, layout


# 自定义槽位保留顺序，预设保持兼容。
def test_custom_slots():
    keys = ["ad_banner", "main_scene", "ad_square"]
    assert [s.key for s in presets.select_slots(6, keys)] == keys
    assert len(presets.select_slots(6, list(presets.GENERAL_SLOTS))) == 10
    assert len(presets.select_slots(4, None)) == 4


# 数量、重复项、未知槽位和缺失主图均拒绝。
@pytest.mark.parametrize("keys", [[], ["main_white"], ["main_white"] * 3,
    ["usage", "ad_banner", "ad_square"], ["main_white", "usage", "unknown"], list(range(11))])
def test_invalid_custom_slots(keys):
    with pytest.raises(ValueError):
        presets.select_slots(6, keys)


# 广告画幅固定，尺寸符合像素下限和最长边上限。
@pytest.mark.parametrize("clarity", ["2k", "4k"])
def test_ad_geometry(clarity):
    assert presets.gen_ratio("xhs", "ad_banner") == "16:9"
    assert presets.gen_ratio("xhs", "ad_square") == "1:1"
    for ratio in ("1:1", "3:4", "16:9"):
        w, h = map(int, presets.generation_size(ratio, clarity).split("x"))
        assert 3_686_400 <= w * h <= 16_777_216
        assert max(w, h) <= 4096
    w, h = presets.output_size("taobao", "ad_banner", clarity)
    assert w / h == 16 / 9
    if clarity == "4k":
        assert max(w, h) == 4096


# 提示词明确语言；英文日文保留合理长度并经过合规过滤。
@pytest.mark.parametrize("language,label", [("en", "English"), ("ja", "日本語"), ("zh", "中文"), ("none", "无文字")])
def test_language_prompts(language, label):
    message = prompts.build_plan_user_text(name="Cup", notes="", platform_name="Amazon", style="minimal",
                                          slots=presets.slots_for_count(4), language=language)
    assert label in message
    copy, _ = prompts.normalize_copy({"title": "Everyday comfort" if language == "en" else "毎日の暮らしを心地よく"},
                                     layout="title_sub", language=language)
    if language == "none":
        assert copy == {"title": "", "subtitle": "", "bullets": []}
    else:
        assert len(copy["title"]) > 10 or language == "zh"
    clean, hits = prompts.normalize_copy({"title": "全网 Everyday"}, layout="title_sub", language=language)
    assert "全网" not in clean["title"]
    if language != "none":
        assert hits


# 无文字规划及最终渲染不得绘制文案。
def test_none_never_renders_text(tmp_path, monkeypatch):
    items, _ = planner.align_items([{"title": "禁止排版"}], presets.slots_for_count(4), name="Cup", language="none")
    assert all(not item["copy"]["title"] for item in items)
    source = tmp_path / "base.png"
    Image.new("RGB", (100, 100), "white").save(source)
    # 错误绘字时直接失败。
    def no_draw(*args, **kwargs):
        pytest.fail("无文字模式不得绘制文案")
    monkeypatch.setattr(layout, "_draw_layout", no_draw)
    layout.render(source, tmp_path / "out.jpg", size=(100, 100), layout="title_sub", blank="top",
                  copy={"title": "不要画"}, language="none")


# mock 生图尊重广告画幅和 4K 清晰度。
def test_mock_ad_size(tmp_path):
    source = tmp_path / "product.png"
    Image.new("RGB", (30, 40), "blue").save(source)
    result = layout.mock_base(source, tmp_path / "base.jpg", ratio="16:9", seed="test", size="4096x2304")
    with Image.open(result) as image:
        assert image.size == (4096, 2304)


# 任务清晰度应传入目录估价，不假定线上两档价格必然不同。
async def test_clarity_estimate(monkeypatch):
    from app.services.billing import estimates
    calls = []
    # 可区分的目录价用于验证档位。
    def catalog(settings, *, size, payload=None):
        calls.append(size)
        return {"2k": 10, "4k": 20}[size]
    monkeypatch.setattr(estimates, "_catalog_image_fen", catalog)
    for clarity, expected in [("2k", 10), ("4k", 20)]:
        assert await estimates.estimate_task_fen(None, TaskRun(domain="studio", task_type="ecom_set_image",
                                                             payload={"clarity": clarity})) == expected
    assert calls == ["2k", "4k"]


# 启动补丁给旧表添加带默认值的新列。
async def test_schema_columns(db_session):
    rows = (await db_session.execute(text("SELECT column_name, column_default FROM information_schema.columns "
                                         "WHERE table_name = 'ecom_sets'"))).all()
    columns = dict(rows)
    assert "language" in columns and "clarity" in columns
    assert "zh" in columns["language"] and "2k" in columns["clarity"]


# 文本和视觉模型调用都应传入语言指令，避免只在 mock 路径可用。
@pytest.mark.parametrize("vision", [False, True])
async def test_planner_model_language(monkeypatch, tmp_path, vision):
    from app.config import get_settings
    import json
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(get_settings(), "model_vision", "vision-test" if vision else "")
    captured = []
    # 使用真实客户端签名捕获参数位置错误。
    async def text_model(system, user, *, temperature, max_tokens, timeout):
        captured.append(user)
        return json.dumps({"items": [{"slot": "main_scene", "title": "Everyday comfort"}]})
    # 视觉客户端接收同样的语言指令及图片。
    async def vision_model(system, user, images, *, response_format):
        captured.append(user)
        assert images[0].startswith("data:image/jpeg;base64,")
        return json.dumps({"items": [{"slot": "main_scene", "title": "Everyday comfort"}]})
    monkeypatch.setattr(planner, "chat_completions", text_model)
    monkeypatch.setattr(planner, "chat_completions_vision", vision_model)
    source = tmp_path / "p.png"
    Image.new("RGB", (20, 20), "blue").save(source)
    result = await planner.plan_set(image_path=source, name="杯子", notes="", platform="amazon", style="minimal",
                                    slots=presets.slots_for_count(4), language="en")
    assert len(captured) == 1 and "English" in captured[0]
    assert result["items"][1]["copy"]["title"] == "Everyday comfort"
    assert result["vision"] is vision


# mock 发布即使启用 OSS 也只能返回本地 URL。
def test_mock_public_url_offline(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services import storage
    from app.services.ecom_set.jobs import public_url
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    # 禁止进入可能上传网络的存储入口。
    def no_network(*args, **kwargs):
        pytest.fail("mock 发布不得联网")
    monkeypatch.setattr(storage, "publish_local", no_network)
    assert public_url(tmp_path / "product.png").startswith("/static/")


# 实际候选字体必须包含英文字母、日文假名与汉字，而不是画成缺字方框。
def test_cjk_fonts_cover_english_and_japanese():
    from pathlib import Path
    from PIL import ImageFont
    checked = []
    for candidates in layout.FONT_CANDIDATES.values():
        for path in candidates:
            if not Path(path).exists():
                continue
            font = ImageFont.truetype(path, 32)
            missing = bytes(font.getmask("\u0378"))
            assert all(bytes(font.getmask(ch)) != missing for ch in "Aaあア日商品")
            checked.append(path)
    assert checked, "测试环境需要 Noto CJK 或 macOS CJK 字体"


# 旧表实际删除新列后执行启动补丁两次，验证 additive 默认值与幂等。
async def test_schema_patch_legacy_table(db_session, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app import main
    await db_session.execute(text("ALTER TABLE ecom_sets DROP COLUMN language, DROP COLUMN clarity"))
    # 复用隔离测试事务，使 DDL 随测试回滚，绝不修改业务库。
    @asynccontextmanager
    async def transaction():
        yield await db_session.connection()
    monkeypatch.setattr(main, "engine", SimpleNamespace(begin=transaction))
    await main._apply_schema_patches()
    await main._apply_schema_patches()
    columns = dict((await db_session.execute(text(
        "SELECT column_name, column_default FROM information_schema.columns WHERE table_name = 'ecom_sets'"
    ))).all())
    assert "zh" in columns["language"] and "2k" in columns["clarity"]


# 精修源隔离、无文字规划/编辑、任务参数及广告 4K mock 成图串联验证。
async def test_refine_to_set_integration(db_session, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from app.config import get_settings
    from app.models_ecom import EcomJob, EcomJobItem
    from app.services import storage
    from app.services.ecom_set import service, jobs, sources
    from conftest import make_user

    user, other = await make_user(db_session), await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    # mock 全链路不得调模型、官价网络或上传 OSS。
    def no_network(*args, **kwargs):
        pytest.fail("mock 全链路不得联网")
    monkeypatch.setattr("app.services.ark.get_ark", no_network)
    monkeypatch.setattr("app.services.billing.estimates.ensure_official_rates", no_network)
    monkeypatch.setattr(storage, "publish_local", no_network)
    # 真实 handler 用当前测试事务，内部 commit 仍只提交 savepoint。
    @asynccontextmanager
    async def session_override():
        yield db_session
    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    image = tmp_path / "refined.png"
    Image.new("RGB", (100, 100), "blue").save(image)
    job = EcomJob(user_id=user.id, kind="refine", status="done", params={})
    db_session.add(job)
    await db_session.flush()
    item = EcomJobItem(job_id=job.id, idx=0, status="succeeded", result_url=storage.rel_static_url(image))
    db_session.add(item)
    await db_session.commit()
    assert await sources.refine_product(db_session, user, job.id, 0) == image
    with pytest.raises(LookupError):
        await sources.refine_product(db_session, other, job.id, 0)
    with pytest.raises(ValueError):
        await sources.refine_product(db_session, user, job.id, 99)
    item.status = "failed"
    await db_session.commit()
    with pytest.raises(ValueError):
        await sources.refine_product(db_session, user, job.id, 0)
    item.status, job.kind = "succeeded", "style"
    await db_session.commit()
    assert await sources.refine_product(db_session, user, job.id, 0) == image
    job.kind = "refine"
    await db_session.commit()
    ecom = await service.create_plan(db_session, user, data=b"", filename="", name="Cup", platform="xhs",
        style="minimal", count=6, notes="", language="none", clarity="4k",
        slot_keys=["main_white", "ad_banner", "ad_square"], source_job_id=job.id, source_item_idx=0)
    assert (ecom.count, ecom.language, ecom.clarity) == (3, "none", "4k")
    assert all(it.layout == "none" and it.blank == "none" and not it.copy_json["title"] for it in ecom.items)
    ecom = await service.update_plan(db_session, user, ecom.id, [{"idx": 1, "title": "不会出现"}])
    assert ecom.items[1].copy_json == {"title": "", "subtitle": "", "bullets": []}
    ecom = await service.start_generation(db_session, user, ecom.id)
    task = await db_session.get(TaskRun, ecom.items[1].task_run_id)
    assert task.payload["clarity"] == "4k" and task.payload["idx"] == 1
    result = await jobs.run_item_job(task)
    assert result["ok"]
    with Image.open(storage.local_path_from_url(result["url"])) as output:
        assert output.size == (4096, 2304)
    ecom = await service.update_copy(db_session, user, ecom.id, 1, {"title": "同样不会出现"})
    assert not ecom.items[1].copy_json["title"]
    options = await service.get_options(db_session)
    assert len(options["slots"]) == 10 and set(options["prices"]) == {"2k", "4k"}
    output = service.to_out(ecom, image_fen=options["prices"]["4k"])
    assert output["total_fen"] == output["image_fen"] * 3


# 参数错误在读取商品图、请求模型和预扣费用之前被拒绝。
@pytest.mark.parametrize("field,value", [("language", "fr"), ("clarity", "8k"), ("slot_keys", [])])
async def test_invalid_plan_before_side_effects(monkeypatch, field, value):
    from types import SimpleNamespace
    from app.services.ecom_set import service
    # 如参数错误仍尝试存图，则说明校验太晚。
    def no_save(*args):
        pytest.fail("无效参数不得写文件")
    monkeypatch.setattr(service, "save_product_image", no_save)
    params = {"data": b"", "filename": "p.png", "name": "Cup", "platform": "taobao", "style": "minimal",
              "count": 6, "notes": "", field: value}
    with pytest.raises(ValueError):
        await service.create_plan(None, SimpleNamespace(id=1), **params)


# HTTP multipart 支持十槽位与精修标识；JSON 槽位解析失败必须是中文错误。
async def test_http_plan_options_and_validation(db_session, monkeypatch, tmp_path):
    import io
    import json
    import httpx
    from fastapi import FastAPI
    from app.api.ecom_set import router
    from app.config import get_settings
    from app.database import get_db
    from app.deps import get_current_user
    from app.services import storage
    from conftest import make_user

    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", True)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    # 所有接口使用回滚测试事务与当前用户。
    async def test_db():
        yield db_session
    # 替换登录依赖，测试本身不创建持久账户。
    async def test_user():
        return user
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_current_user] = test_user
    data = io.BytesIO()
    Image.new("RGB", (30, 30), "blue").save(data, format="PNG")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        options = await client.get("/api/tools/ecom-set/options")
        assert options.status_code == 200
        assert len(options.json()["slots"]) == 10
        for invalid in ("{", "null", "123", '"main_white"'):
            response = await client.post("/api/tools/ecom-set/plan", data={"name": "Cup", "slots": invalid})
            assert response.status_code == 400 and response.json()["detail"] == "槽位格式错误"
        response = await client.post("/api/tools/ecom-set/plan", data={"name": "Cup", "language": "en",
            "clarity": "4k", "slots": json.dumps(list(presets.GENERAL_SLOTS))},
            files={"file": ("product.png", data.getvalue(), "image/png")})
        assert response.status_code == 200, response.text
        output = response.json()
        assert output["count"] == len(output["items"]) == 10
        assert output["language"] == "en" and output["clarity"] == "4k"
        # 十张编辑后仍能按语言保存，接口 schema 不再限制八张。
        response = await client.put(f'/api/tools/ecom-set/{output["id"]}/plan', json={"items": [
            {"idx": item["idx"], "title": "Everyday comfort", "subtitle": "Made for daily life", "bullets": []}
            for item in output["items"]]})
        assert response.status_code == 200
        assert response.json()["items"][1]["title"] == "Everyday comfort"


# 宽幅广告文案应限制在预留的上方区域，避免遮住下半部商品。
def test_banner_text_fits_reserved_area(monkeypatch):
    bottoms = []
    original = layout.draw_lines
    # 记录文字块实际占用高度并继续执行真实绘制。
    def draw(*args, **kwargs):
        bottom = original(*args, **kwargs)
        bottoms.append(bottom)
        return bottom
    monkeypatch.setattr(layout, "draw_lines", draw)
    image = Image.new("RGBA", (1600, 900), "white")
    layout._draw_layout(image, layout="title_sub", blank="top", title="毎日の暮らしを心地よく",
                        subtitle="日常に寄り添うデザイン", bullets=[])
    assert max(bottoms) < image.height * 0.42


# 真实生图分支必须同时传递像素、广告画幅和结构化产品参考图。
async def test_generate_base_provider_arguments(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.config import get_settings
    from app.models_ecom import EcomSet, EcomSetItem
    from app.services import storage
    from app.services.ecom_set import jobs
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    captured = {}
    # 捕获上游请求，真实生成分支中不请求网络。
    async def generate(prompt, negative, refs, **kwargs):
        captured.update(kwargs)
        assert refs == ["/static/product.png"] and "用于后期排版文字" not in prompt
        return SimpleNamespace(local_url="/static/generated.png", remote_url=None)
    # 用量原始元数据应保留清晰度，供无上游账单时按正确档位计费。
    async def usage(db, **kwargs):
        assert kwargs["extra_raw"]["size"] == "4k"
    # 下载步骤只验证转存目标，避免测试访问存储网络。
    async def local(url, dest):
        return dest
    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(gen_image=generate))
    monkeypatch.setattr("app.services.drama.billing_util.record_seedream_image_usage", usage)
    monkeypatch.setattr(storage, "ensure_local_media", local)
    ecom = EcomSet(id=1, user_id=1, language="none", clarity="4k", style="minimal",
                   product_image_path="product.png", product_image_url="/static/product.png")
    item = EcomSetItem(idx=0, slot="ad_banner", ratio="16:9", blank="top", scene="桌面")
    await jobs.generate_base(None, ecom, item)
    assert captured["size"] == "4096x2304" and captured["aspect_ratio"] == "16:9"
    assert captured["structured_refs"] is True
