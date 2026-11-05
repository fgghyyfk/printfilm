"""E5 模型边界、中文 API 校验、失败原子性与老库补丁。"""
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import UsageEvent
from app.models_ecom import EcomJob, EcomSet, EcomSetItem
from app.models_tasks import TaskRun
from app.services import storage
from app.services.ecom_jobs import jobs, service, translate
from app.services.ecom_set import translate as set_translate
from test_ecom_translate import image_bytes, setup_media


# 文案按 idx 匹配，空字段和要点数必须保持一致。
def test_parse_set_copy_preserves_shape():
    items = [EcomSetItem(idx=1, layout="title_sub", copy_json={"title":"杯子", "subtitle":"", "bullets":[]}),
             EcomSetItem(idx=3, layout="title_bullets", copy_json={"title":"", "subtitle":"", "bullets":["日常"]})]
    rows = [{"idx":3, "title":"", "subtitle":"", "bullets":["Daily"]},
            {"idx":1, "title":"Cup", "subtitle":"", "bullets":[]}]
    result = set_translate.parse_copies('```json\n' + json.dumps({"items":rows}) + '\n```', items, "en")
    assert result[0]["title"] == "Cup" and result[1]["bullets"] == ["Daily"]
    for invalid in [[], rows[:1], [rows[0], rows[0]], [rows[0], {**rows[1], "title":""}],
                    [rows[0], {**rows[1], "subtitle":"invented"}],
                    [{**rows[0], "bullets":[]}, rows[1]], [rows[0], {**rows[1], "idx":True}]]:
        with pytest.raises(ValueError, match="翻译结果格式错误"):
            set_translate.parse_copies(json.dumps({"items":invalid}), items, "en")


# 真实 OCR 边界发送图片内容和目标语言/词表，不回落为文字猜测。
async def test_vision_call(monkeypatch, tmp_path):
    setup_media(monkeypatch, tmp_path, False)
    monkeypatch.setattr(get_settings(), "model_vision", "vision-test")
    source = tmp_path / "source.png"
    source.write_bytes(image_bytes())
    captured = {}

    # 视觉边界返回标准 JSON。
    async def vision(system, text, images, **kwargs):
        captured.update(system=system, text=text, images=images)
        return '{"translations":[{"source":"杯子","target":"Cup"}]}'

    monkeypatch.setattr("app.services.llm_client.chat_completions_vision", vision)
    assert await translate.recognize(source, language="en", glossary="Brand 保留") == [{"source":"杯子", "target":"Cup"}]
    assert captured["images"][0].startswith("data:image/jpeg;base64,")
    assert json.loads(captured["text"]) == {"language":"English", "glossary":"Brand 保留"}
    assert '"translations":[]' in captured["system"]


# 真实图像生成带 source 结构化引用，反向提示词不会禁止译文字体。
async def test_translate_provider_reference(monkeypatch, tmp_path):
    from app.services import ark
    setup_media(monkeypatch, tmp_path, False)
    captured = {}

    # 捕获图片生成请求参数。
    async def gen_image(prompt, negative, refs, **kwargs):
        captured.update(negative=negative, refs=refs, kwargs=kwargs)
        return ark.ImageResult(local_url="/static/out.png")

    # 仅检查调用契约，不下载网络图片。
    async def local(url, path):
        return path

    monkeypatch.setattr(ark, "get_ark", lambda: SimpleNamespace(gen_image=gen_image))
    monkeypatch.setattr(storage, "ensure_local_media", local)
    item = SimpleNamespace(idx=0, inputs=[{"role":"source", "url":"https://example.com/source.png"}], prompt="只换译文", ratio="4:3", size="128x96")
    await jobs.generate_image(None, SimpleNamespace(id=1, user_id=1, kind="translate", params={}), item)
    assert captured["refs"] == ["https://example.com/source.png"]
    assert captured["kwargs"]["structured_refs"] is True and captured["kwargs"]["reference_roles"] == ["source"]
    assert captured["negative"] == translate.NEGATIVE_PROMPT and "文字" not in captured["negative"]


# 翻译请求校验返回中文短句，坏语言不能创建任务或收费。
async def test_set_translate_api_validation(db_session):
    from app.api.ecom_set import router
    from app.database import get_db
    from app.deps import get_current_user
    from conftest import make_user
    user = await make_user(db_session)

    # 鉴权使用当前用户。
    async def owner():
        return user

    # 接口使用隔离事务。
    async def database():
        yield db_session

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for body in ({"language":"fr"}, {"language":"en", "extra":True}, {}):
            response = await client.post('/api/tools/ecom-set/1/translate', json=body)
            assert response.status_code == 400 and isinstance(response.json()["detail"], str)
        response = await client.post('/api/tools/ecom-set/1/translate', json={"language":"en"})
        assert response.status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.requested_by == user.id)) == 0


# OCR 格式错误应整批回滚，不误标免费跳过。
async def test_ocr_failure_rolls_back(db_session, monkeypatch, tmp_path):
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)

    # 模拟模型输出缺少译文列表。
    async def invalid(*args, **kwargs):
        return translate.parse_translations('{}')

    monkeypatch.setattr(translate, "recognize", invalid)
    with pytest.raises(ValueError, match="翻译结果格式错误"):
        await service.create_job(db_session, user, kind="translate", uploads=[(image_bytes(), "a.png")], params={"language":"en"})
    assert await db_session.scalar(select(func.count()).select_from(EcomJob)) == 0
    assert not list(tmp_path.rglob('input_*.png'))
    assert await db_session.scalar(select(func.count()).select_from(TaskRun)) == 0


# 文字模型只翻译 copy 并保留语言与条目索引。
async def test_set_text_model_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "ark_mock", False)
    captured = {}

    # 文本边界返回英文文案。
    async def chat(system, user, **kwargs):
        captured.update(data=json.loads(user))
        return '{"items":[{"idx":2,"title":"Cup","subtitle":"Daily","bullets":["Portable"]}]}'

    monkeypatch.setattr("app.services.llm_client.chat_completions", chat)
    items = [EcomSetItem(idx=2, layout="title_bullets", copy_json={"title":"杯子", "subtitle":"日常", "bullets":["便携"]})]
    assert (await set_translate.translate_copies(items, "en"))[0]["bullets"] == ["Portable"]
    assert captured["data"]["language"] == "English" and captured["data"]["items"][0]["idx"] == 2


# 重排失败时新套图、任务与费用全部回滚，源底图仍可用。
async def test_set_render_failure_atomic(db_session, monkeypatch, tmp_path):
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)
    base = tmp_path / "base.png"
    base.write_bytes(image_bytes())
    source = EcomSet(user_id=user.id, status="done", name="杯子", count=1, language="zh", clarity="2k")
    source.items = [EcomSetItem(idx=0, slot="selling_1", layout="title_sub", blank="top", ratio="3:4", scene="桌面",
        copy_json={"title":"杯子", "subtitle":"", "bullets":[]}, base_path=str(base), status="succeeded")]
    db_session.add(source)
    await db_session.commit()
    user_id, source_id, balance = user.id, source.id, user.balance_fen

    # 模拟本地排版失败。
    async def fail(*args):
        raise OSError("测试排版失败")

    monkeypatch.setattr(set_translate, "render_final", fail)
    with pytest.raises(OSError):
        await set_translate.translate_set(db_session, user, source_id, "ja")
    assert await db_session.scalar(select(func.count()).select_from(EcomSet)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TaskRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(UsageEvent)) == 0
    await db_session.refresh(user)
    assert user.balance_fen == balance and user.frozen_fen == 0
    assert base.is_file()
    assert not list((tmp_path / 'ecom' / f'u{user_id}').glob('s*'))


# 老库来源字段补列幂等，兼容重复启动。
async def test_translate_schema_patch(db_session, monkeypatch):
    from app import main
    connection = await db_session.connection()
    await connection.execute(text('ALTER TABLE ecom_sets DROP COLUMN source_set_id'))

    # 限定启动 DDL 在回滚事务里。
    @asynccontextmanager
    async def begin():
        yield connection

    monkeypatch.setattr(main, "engine", SimpleNamespace(begin=begin))
    await main._apply_schema_patches()
    await main._apply_schema_patches()
    assert "source_set_id" in await main._pg_columns(connection, "ecom_sets")


# 整批识字到达截止时间时取消未完请求，不创建生图任务并回收临时媒体。
async def test_translate_batch_deadline(db_session, monkeypatch, tmp_path):
    import asyncio
    from conftest import make_user
    user = await make_user(db_session)
    setup_media(monkeypatch, tmp_path)
    monkeypatch.setattr(translate, "BATCH_OCR_TIMEOUT", .01, raising=False)

    # 模拟视觉上游长期不返回。
    async def slow(*args, **kwargs):
        await asyncio.sleep(.1)
        return [{"source":"杯子", "target":"Cup"}]

    monkeypatch.setattr(translate, "recognize", slow)
    with pytest.raises(ValueError, match="图片识字超时"):
        await service.create_job(db_session, user, kind="translate", uploads=[(image_bytes(), "a.png")], params={"language":"en"})
    assert await db_session.scalar(select(func.count()).select_from(EcomJob)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TaskRun)) == 0
    assert not list(tmp_path.rglob('input_*.png'))
