"""E5 图片翻译的解析、参数、提示词、离线任务与套图复制回归。"""
from contextlib import asynccontextmanager
import io
import json

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import func, select

from app.config import get_settings
from app.models import UsageEvent
from app.models_ecom import EcomJob, EcomSet, EcomSetItem
from app.models_tasks import TaskRun
from app.services import storage
from app.services.ecom_jobs import jobs, kinds, service


# 构造无需外部素材的原始商品图。
def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), "navy").save(buffer, format="PNG")
    return buffer.getvalue()


# 兼容 Markdown、前后说明、字段别名与空文字列表。
@pytest.mark.parametrize("raw,expected", [
    ('```json\n{"translations":[{"source":"杯子","target":"Cup"}]}\n```', [{"source": "杯子", "target": "Cup"}]),
    ('说明 {"translations":[{"original":"杯子","translated":"Cup"}]} 完成', [{"source": "杯子", "target": "Cup"}]),
    ('{"translations":[]}', []),
])
def test_translation_json_tolerant(raw, expected):
    from app.services.ecom_jobs.translate import parse_translations
    assert parse_translations(raw) == expected


# 结构错误不能被误判成无文字而免费跳过。
@pytest.mark.parametrize("raw", ['no json', '{}', '{"translations":null}', '{"translations":{}}',
    '{"translations":[{}]}', '{"translations":[{"source":"杯子","target":""}]}',
    '{"translations":[{"source":1,"target":"Cup"}]}'])
def test_translation_json_invalid(raw):
    from app.services.ecom_jobs.translate import parse_translations
    with pytest.raises(ValueError):
        parse_translations(raw)


# 新 kind 严格限定语言、数量、词表和额外字段。
@pytest.mark.parametrize("count,params", [(0, {"language":"en"}), (21, {"language":"en"}),
    (1, {}), (1, {"language":"fr"}), (1, {"language":"none"}),
    (1, {"language":"ja", "glossary":1}), (1, {"language":"en", "glossary":"x" * 2001}),
    (1, {"language":"en", "unknown":True})])
def test_translate_invalid_params(count, params):
    with pytest.raises(ValueError):
        kinds.validate("translate", count, params)


# 译文为明确替换表，参考图角色与反向提示词不再禁止画面文字。
def test_translate_prompt():
    from app.services.ecom_jobs.translate import build_prompt, NEGATIVE_PROMPT
    prompt = build_prompt({"language":"ja", "glossary":"KaiKou", "translations":[{"source":"杯子", "target":"カップ"}]})
    for text in ("版式", "配色", "商品不变", "杯子", "カップ", "KaiKou", "仅", "图1"):
        assert text in prompt
    assert "文字" not in NEGATIVE_PROMPT


# 翻译任务登记、估价复用文本规划价格，mock 不能拉线上目录。
async def test_translate_handler_estimate(monkeypatch):
    from app.services.billing import estimates
    from app.services.tasks.handlers import get_task_handler
    settings = get_settings().model_copy(update={"ark_mock":True, "ecom_set_plan_fen":7})

    # 明确禁止远程价格读取。
    async def forbid(*args):
        raise AssertionError("禁止联网")

    monkeypatch.setattr(estimates, "ensure_official_rates", forbid)
    assert get_task_handler("studio", "ecom_set_translate") is not None
    assert await estimates.estimate_task_fen(None, TaskRun(domain="studio", task_type="ecom_set_translate"), settings) == 7


# 准备隔离的媒体根目录与 mock 开关。
def setup_media(monkeypatch, tmp_path, mock=True):
    monkeypatch.setattr(get_settings(), "ark_mock", mock)
    monkeypatch.setattr(storage, "project_dir", lambda _: tmp_path)
    monkeypatch.setattr(storage, "STATIC_ROOT", tmp_path)


# 无视觉模型时接口返回中文，且不建任何任务。
async def test_translate_requires_vision(db_session, monkeypatch):
    from app.api.ecom_jobs import router
    from app.database import get_db
    from app.deps import get_current_user
    from conftest import make_user
    user = await make_user(db_session)
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    monkeypatch.setattr(get_settings(), "model_vision", "")

    # HTTP 层使用当前隔离事务。
    async def db_override():
        yield db_session

    # HTTP 层使用当前测试用户。
    async def user_override():
        return user

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post('/api/tools/ecom-jobs/translate', files={"files":("a.png", image_bytes(), "image/png")}, data={"params":json.dumps({"language":"en"})})
    assert response.status_code == 400 and "视觉模型" in response.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(EcomJob).where(EcomJob.user_id == user.id)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.requested_by == user.id)) == 0


# 无文字项不冻结、不记录用量、不生成图片；整批跳过仍是正常终态。
async def test_translate_no_text_free(db_session, monkeypatch, tmp_path):
    from app.services.ecom_jobs import translate
    from conftest import make_user
    user = await make_user(db_session, balance_fen=0)
    setup_media(monkeypatch, tmp_path)

    # 模拟视觉模型明确返回空文字列表。
    async def recognize(*args, **kwargs):
        return []

    monkeypatch.setattr(translate, "recognize", recognize)
    job = await service.create_job(db_session, user, kind="translate", uploads=[(image_bytes(), "a.png")], params={"language":"en"})
    assert job.status == "done"
    assert job.items[0].status == "skipped" and job.items[0].error == "无可翻译文字"
    assert job.items[0].task_run_id is None and job.items[0].result_url is None
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.requested_by == user.id)) == 0
    assert await db_session.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.user_id == user.id)) == 0
    await db_session.refresh(user)
    assert user.balance_fen == user.frozen_fen == 0


# 混合批次只为有字图片创建任务，并在完成后正确聚合 skipped。
async def test_translate_mixed_batch(db_session, monkeypatch, tmp_path):
    from app.services.ecom_jobs import translate
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)
    calls = []

    # 首图有字、次图无字。
    async def recognize(*args, **kwargs):
        calls.append(True)
        return [{"source":"杯子", "target":"Cup"}] if len(calls) == 1 else []

    # 完整 handler 复用隔离事务。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr(translate, "recognize", recognize)
    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    job = await service.create_job(db_session, user, kind="translate", uploads=[(image_bytes(), "a.png"), (image_bytes(), "b.png")], params={"language":"en"})
    assert [item.status for item in job.items] == ["queued", "skipped"]
    assert job.items[1].task_run_id is None
    task = await db_session.get(TaskRun, job.items[0].task_run_id)
    job.items[0].size = "160x120"
    await db_session.commit()
    result = await jobs.run_item_job(task)
    assert result["ok"] and (await service.get_job(db_session, user, job.id)).status == "done"


# mock 输入、识字与出图全程本地运行，标记示意结果。
async def test_translate_mock_offline(db_session, monkeypatch, tmp_path):
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)

    # mock 的所有外部边界都必须绕过。
    def forbid(*args, **kwargs):
        raise AssertionError("mock 禁止联网")

    # 绑定 handler 到当前隔离事务。
    @asynccontextmanager
    async def session_override():
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_override)
    monkeypatch.setattr("app.services.llm_client.chat_completions_vision", forbid)
    monkeypatch.setattr("app.services.ark.get_ark", forbid)
    monkeypatch.setattr(storage, "publish_local", forbid)
    job = await service.create_job(db_session, user, kind="translate", uploads=[(image_bytes(), "a.png")], params={"language":"ja", "glossary":"Brand"})
    assert job.params["mock"] is True and "Brand" in job.items[0].prompt
    task = await db_session.get(TaskRun, job.items[0].task_run_id)
    job.items[0].size = "160x120"
    await db_session.commit()
    result = await jobs.run_item_job(task)
    with Image.open(storage.local_path_from_url(result["url"])) as image:
        assert image.format == "PNG" and image.size == (160, 120)
    assert (await service.get_job(db_session, user, job.id)).status == "done"


# 套图翻译复用底图，复制展示字段但不沿用生图任务或视频项目。
async def test_translate_set_copy_and_charge(db_session, monkeypatch, tmp_path):
    from app.services.ecom_set.translate import translate_set
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)
    base = tmp_path / 'base.png'
    base.write_bytes(image_bytes())
    source = EcomSet(user_id=user.id, status="done", name="杯子", platform="taobao", style="minimal", count=1,
        language="zh", clarity="2k", product_image_path=str(base), product_image_url=storage.rel_static_url(base),
        plan={"product":{"name":"杯子"}, "warnings":[], "vision":False}, project_id=42)
    source.items = [EcomSetItem(idx=0, slot="hero", layout="title_sub", blank="top", ratio="1:1", scene="桌面",
        copy_json={"title":"杯子", "subtitle":"日常", "bullets":[]}, base_path=str(base), base_url=storage.rel_static_url(base), status="succeeded")]
    db_session.add(source)
    await db_session.commit()
    source_id = source.id
    translated = await translate_set(db_session, user, source_id, "en")
    assert translated.id != source_id and translated.source_set_id == source_id and translated.language == "en"
    assert translated.status == "done" and translated.project_id is None and translated.clarity == "2k"
    item = translated.items[0]
    assert item.base_path == str(base) and item.base_url == source.items[0].base_url
    assert item.scene == "桌面" and item.status == "succeeded" and item.task_run_id is None
    assert item.copy_json["title"] != "杯子" and storage.local_path_from_url(item.final_url).is_file()
    tasks = list((await db_session.scalars(select(TaskRun).where(TaskRun.requested_by == user.id))).all())
    assert len(tasks) == 1 and tasks[0].task_type == "ecom_set_translate"
    events = list((await db_session.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id))).all())
    assert len(events) == 1 and events[0].capability == "llm" and events[0].billing_key == "ecom_set_translate"
    assert events[0].charge_fen == get_settings().ecom_set_plan_fen
    assert source.items[0].copy_json["title"] == "杯子" and source.project_id == 42
    with pytest.raises(LookupError):
        await translate_set(db_session, await make_user(db_session), source_id, "ja")
    with pytest.raises(ValueError):
        await translate_set(db_session, user, source_id, "none")
