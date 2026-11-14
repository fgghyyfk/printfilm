"""仅undo的双连接原子性：普通改稿与第二条撤销不能被陈旧ORM覆盖。"""
import asyncio
from contextlib import suppress

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import projects as api
from app.config import get_settings
from app.models import Project, Shot, Template, User
from app.schemas import ProjectUpdate, ShotUpdate
from app.services.compliance.changes import clean_fields
from tests.conftest import _resolve_test_database_url, make_user


@pytest.mark.parametrize("owner,other_action,done_parent", [
    ("shot", "manual", False), ("project", "manual", False),
    ("shot", "undo", False), ("project", "undo", False),
    ("shot", "manual", True),
])
async def test_undo_serializes_with_committed_edits_and_other_undo(db_session, tmp_path, monkeypatch, owner, other_action, done_parent):
    """在首次owner读取后交错两个真实PG连接，核对最终文本和两条审计状态。"""
    # db_session 只初始化隔离测试schema；竞争数据在独立连接提交，最终显式删除。
    # 独立连接也必须落在 <库名>_test，不能直连业务库。
    engine = create_async_engine(_resolve_test_database_url(get_settings().database_url)[0])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original = "😀最新，最强"
    project_id = user_id = None
    tasks = []
    monkeypatch.setattr("app.services.storage.project_dir", lambda _id: tmp_path)
    try:
        async with sessions() as setup:
            user = await make_user(setup)
            user_id = user.id
            tpl = Template(id=f"audit-race-{user.id}", name="撤销并发", category=["带货"], style_prefix="x")
            setup.add(tpl)
            await setup.flush()
            project_fields, project_audit = clean_fields({"title": original})
            project = Project(user_id=user.id, template_id=tpl.id, source_text="测试", title=project_fields["title"], compliance_changes=project_audit, status="DONE" if done_parent else "SCRIPT_READY", final_video_url="/static/final.mp4" if done_parent else None, pipeline_mode="image_text")
            setup.add(project)
            await setup.flush()
            project_id = project.id
            script = f"@duration:5\n画面：暖壶\n【旁白】{original}"
            fields, audit = clean_fields({"narration": original, "segment_script": script, "video_prompt": script})
            shot = Shot(project_id=project.id, shot_no=1, **fields, compliance_changes=audit)
            setup.add(shot)
            await setup.commit()
            shot_id = shot.id
            template_id = tpl.id
        async with sessions() as first, sessions() as second:
            # 强引用两侧旧ORM直至测试结束，避免身份映射弱引用被GC后假性覆盖新读。
            stale_objects = []
            for session in (first, second):
                stale_objects.append(await session.get(Project, project_id))
                stale_objects.append(await session.get(Shot, shot_id))
            read, resume = asyncio.Event(), asyncio.Event()
            get_owned = api._get_owned_project

            async def pause_first_read(db, project_id, user, **kwargs):
                """A完成owner读取后停住，让B发起真实普通PATCH或另一条undo。"""
                value = await get_owned(db, project_id, user, **kwargs)
                if db is first:
                    read.set()
                    await resume.wait()
                return value

            monkeypatch.setattr(api, "_get_owned_project", pause_first_read)
            field = "narration" if owner == "shot" else "title"

            async def patch(db, *, change_id=None):
                """调用现有生产PATCH，连接/事务各自独立。"""
                who = await db.get(User, user_id)
                payload = {"compliance_undo": {"field": field, "change_id": change_id}} if change_id is not None else {field: "手动新稿，保留最强"}
                if owner == "shot":
                    return await api.update_shot(project_id, shot_id, ShotUpdate(**payload), db=db, user=who)
                return await api.update_project(project_id, ProjectUpdate(**payload), db=db, user=who)

            first_task = asyncio.create_task(patch(first, change_id="0"))
            tasks.append(first_task)
            await asyncio.wait_for(read.wait(), timeout=5)
            second_task = asyncio.create_task(patch(second, change_id="1" if other_action == "undo" else None))
            tasks.append(second_task)
            # 旧代码允许B先提交，再由A覆盖；修复后B应等待A持有的目标行锁。
            await asyncio.wait({second_task}, timeout=0.1)
            resume.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        async with sessions() as check:
            saved = await check.get(Shot if owner == "shot" else Project, shot_id if owner == "shot" else project_id)
            # 带货模板分镜手改稿按线上规则自动替换极限词；项目标题手改不做替换。
            manual = "手动新稿，保留强劲" if owner == "shot" else "手动新稿，保留最强"
            assert getattr(saved, field) == (original if other_action == "undo" else manual)
            flags = [change["undone"] for change in saved.compliance_changes[field]["changes"]]
            assert flags == ([True, True] if other_action == "undo" else [True, False])
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        if project_id is not None:
            async with sessions() as cleanup:
                await cleanup.execute(delete(Shot).where(Shot.project_id == project_id))
                await cleanup.execute(delete(Project).where(Project.id == project_id))
                await cleanup.execute(delete(Template).where(Template.id == template_id))
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.commit()
        await engine.dispose()
