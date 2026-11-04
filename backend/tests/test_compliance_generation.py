"""三条真实写稿保存链路均保留替换审计，模型与媒体全部本地替身。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
from sqlalchemy import select

from app.models import Project, Shot, Template
from tests.conftest import make_user


@pytest.mark.parametrize("path", ["pipeline", "daihuo", "avatar"])
async def test_generated_changes_saved_at_each_entry(db_session, monkeypatch, tmp_path, path):
    """执行生产生成入口，重读数据库而非只测解析返回值。"""
    from app.services import pipeline, kepu_avatar_look
    from app.services.daihuo import service, script
    from app.models_ecom import EcomSet

    user = await make_user(db_session)
    tpl = Template(id="audit-generate", name="广告模板", category=["带货"], style_prefix="test")
    db_session.add(tpl)
    await db_session.flush()
    project = Project(user_id=user.id, template_id=tpl.id, source_text="口播主题", title="未命名作品", pipeline_mode="avatar" if path == "avatar" else "image_text")
    if path == "avatar":
        project.avatar_script_style = "opinion"
    db_session.add(project)
    await db_session.commit()

    @asynccontextmanager
    async def session_factory():
        """复用隔离测试事务，不连接其他数据库。"""
        yield db_session

    monkeypatch.setattr("app.database.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(kepu_avatar_look, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(pipeline, "_record_usage_est", AsyncMock())
    monkeypatch.setattr(pipeline, "publish_progress", AsyncMock())
    monkeypatch.setattr(pipeline, "_set_status", AsyncMock())
    raw_narration = "😀最好用的暖壶，顶级设计，续航最多12小时。"
    raw_title = "顶级好物"
    if path == "pipeline":
        monkeypatch.setattr(service, "project_has_ecom_set", AsyncMock(return_value=False))
        monkeypatch.setattr(pipeline, "_project_is_avatar", AsyncMock(return_value=False))
        plan = SimpleNamespace(text=raw_narration, overlay_title=raw_title, overlay_subtitle="第一印象", segment_script=f"@duration:5\n【旁白·自然语速】{raw_narration}", video_prompt="", duration=5, img_prompt="白底暖壶", shot=1, camera="static", bgm="neutral")
        ark = SimpleNamespace(chat_storyboard=AsyncMock(return_value=SimpleNamespace(shots=[plan], character_bible="", bgm_lock="neutral")), _sanitize_seedream_prompt=lambda text: text)
        monkeypatch.setattr(pipeline, "get_ark", lambda: ark)
        await pipeline._script_stage(project.id)
    elif path == "avatar":
        monkeypatch.setattr(kepu_avatar_look.avatar_ai, "chat_json", AsyncMock(return_value={"title": raw_title, "title2": "唯一选择", "segments": [{"narration": raw_narration, "title": raw_title, "keywords": ["顶级设计"], "scene": "白底暖壶"}]}))
        await kepu_avatar_look.write_script(project.id)
    else:
        ecom = EcomSet(user_id=user.id, name="暖壶")
        db_session.add(ecom)
        await db_session.flush()
        project.ecom_set_id = ecom.id
        project.title = ecom.name
        await db_session.commit()
        items = [{"idx": 0, "title": raw_title, "subtitle": "第一印象", "scene": "白底暖壶", "base_path": str(tmp_path / "base.jpg"), "slot_label": "主图"}]
        parsed = script.parse_segments(json.dumps({"title": raw_title, "segments": [{"idx": 0, "title": raw_title, "narration": raw_narration}]}, ensure_ascii=False), items, name=ecom.name)
        monkeypatch.setattr(service, "usable_items", lambda _: items)
        monkeypatch.setattr(service, "write_segments", AsyncMock(return_value=parsed))
        monkeypatch.setattr(service, "pad_to_frame", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.storage, "project_dir", lambda _: tmp_path)
        monkeypatch.setattr(service.storage, "rel_static_url", lambda _: "/static/frame.jpg")
        await service.build_daihuo_shots(project.id)
    shots = (await db_session.execute(select(Shot).where(Shot.project_id == project.id))).scalars().all()
    assert len(shots) == 1
    saved = shots[0]
    await db_session.refresh(saved)
    assert saved.narration == "😀好用的暖壶，高端设计，续航最多12小时。"
    assert saved.compliance_changes["narration"]["original"] == raw_narration
    assert saved.compliance_changes["overlay_title"]["original"] == raw_title
    if path != "pipeline":
        await db_session.refresh(project)
        assert project.compliance_changes["title"]["original"] == raw_title
    if path == "avatar":
        assert project.compliance_changes["avatar_title2"]["original"] == "唯一选择"
