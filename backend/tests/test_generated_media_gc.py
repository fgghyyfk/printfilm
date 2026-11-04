"""仅注入tmp_path生成根，绝不访问用户generated；保守引用与删除事务边界。"""
import json
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import projects
from app.models import Project, User, Work
from app.models_drama import DramaProject
from app.services import generated_media_gc as gc
from tests.conftest import make_task
from tests.test_kepu_task_recovery import seed_project
from tests.tts_database import db_session


def file_at(root, relative, *, stamp=None):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"preserve unless proven orphan")
    if stamp is not None:
        os.utime(path, (stamp, stamp))
    return path


async def test_only_old_unreferenced_orphan_can_be_explicitly_removed(db_session, tmp_path):
    root = tmp_path / "generated"
    root.mkdir()
    now = time.time() + 45 * 86400  # 注入时钟使测试ctime也满足保留期，不更改系统时间。
    user, project, _ = await seed_project(db_session)
    db_session.add(DramaProject(id=project.id, user_id=user.id, title="同号漫剧"))
    implicit = file_at(root, f"p{project.id}/full_narration.mp3")
    history = file_at(root, "p990002/history.mp4")
    work_file = file_at(root, "p990003/work.mp4")
    avatar = file_at(root, "p990004/avatar.png")
    active = file_at(root, "p990005/intermediate.png")
    part = file_at(root, "p990008/unfinished.mp4.part")
    new_file = file_at(root, "p990009/new.mp4", stamp=now)
    p0 = file_at(root, "p0/tool.mp4")
    orphan = file_at(root, "p990010/orphan.mp4")
    outside = file_at(tmp_path, "outside/do-not-touch.mp4")
    (root / "p990006").mkdir()
    (root / "p990006" / "link.mp4").symlink_to(outside)
    (root / "p990007").symlink_to(outside.parent, target_is_directory=True)
    user.avatar_url = "/static/generated/p990004/avatar.png?signature=synthetic-secret-value"
    terminal = await make_task(db_session, user, status="succeeded")
    terminal.result_payload = {"video_versions": json.dumps({"video_url": "/static/generated/p990002/history.mp4"}).replace("/", "\\/")}
    pending = await make_task(db_session, user, status="awaiting_review")
    pending.payload = {"project_id": 990005, "finalizing_until": "later"}
    db_session.add(Work(user_id=user.id, project_id=project.id, title="旧作品",
                       video_url="https://old-cdn.test/kepu/generated/p990003/work.mp4?signature=synthetic-secret-value"))
    await db_session.commit()
    try:
        preview = await gc.audit_generated_orphans(db_session, root=root, now=now)
        assert preview["ok"], preview
        assert preview["dry_run"] and preview["deleted"] == []
        assert preview["candidates"] == [str(orphan)] and orphan.exists()
        applied = await gc.audit_generated_orphans(db_session, root=root, now=now, apply=True)
        assert applied["deleted"] == [str(orphan)]
        assert all(path.exists() for path in [implicit, history, work_file, avatar, active, part, new_file, p0, outside])
        assert "signature=" not in json.dumps(applied) and "synthetic-secret-value" not in json.dumps(applied)
    finally:
        await db_session.execute(delete(Work).where(Work.user_id == user.id))
        await db_session.commit()


async def test_new_reference_before_apply_and_incomplete_audit_both_keep_file(db_session, tmp_path, monkeypatch):
    root = tmp_path / "generated"
    root.mkdir()
    now = time.time() + 45 * 86400
    candidate = file_at(root, "p990020/old.mp4")
    user, _, _ = await seed_project(db_session)
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    real_audit = gc.collect_reference_audit
    calls = 0
    async def audit_then_concurrent_reference(db, audit_root):
        nonlocal calls
        result = await real_audit(db, audit_root)
        calls += 1
        if calls == 1:
            async with sessions() as writer:
                row = await writer.get(User, user.id)
                row.avatar_url = "/static/generated/p990020/old.mp4"
                await writer.commit()
        return result
    monkeypatch.setattr(gc, "collect_reference_audit", audit_then_concurrent_reference)
    result = await gc.audit_generated_orphans(db_session, root=root, now=now, apply=True)
    assert result["ok"] and result["deleted"] == [] and candidate.exists()
    assert calls == 2
    async def broken(*args):
        return gc.ReferenceAudit(complete=False, error="reference_audit_failed")
    monkeypatch.setattr(gc, "collect_reference_audit", broken)
    blocked = await gc.audit_generated_orphans(db_session, root=root, now=now, apply=True)
    assert not blocked["ok"] and candidate.exists()
    from unittest.mock import AsyncMock
    monkeypatch.setattr(gc, "collect_reference_audit", real_audit)
    monkeypatch.setattr(db_session, "execute", AsyncMock(side_effect=RuntimeError("synthetic-secret-value")))
    query_failed = await gc.audit_generated_orphans(db_session, root=root, now=now, apply=True)
    assert not query_failed["ok"] and query_failed["error"] == "reference_audit_failed"
    assert candidate.exists() and "synthetic-secret-value" not in json.dumps(query_failed)


async def test_prose_punctuation_and_encoded_filename_references_keep_original_files(db_session, tmp_path):
    root = tmp_path / "generated"
    root.mkdir()
    now = time.time() + 45 * 86400
    retained = [file_at(root, f"p990030/{name}") for name in [
        "old.mp4", "a,b.mp4", "image?v1.png", "image#v1.png", "image%3Fv1.png",
        "local?#%.mp4", "plain.mp4",
    ]]
    orphan = file_at(root, "p990030/orphan.mp4")
    user, _, _ = await seed_project(db_session)
    task = await make_task(db_session, user, status="succeeded")
    task.result_payload = {"notes": [
        "备用地址 /static/generated/p990030/old.mp4, 后续说明",
        "备用地址 /static/generated/p990030/a,b.mp4, 后续说明",
        "/static/generated/p990030/image%3Fv1.png?signature=synthetic-secret#real-fragment",
        "https://cdn.test/generated/p990030/image%23v1.png?signature=synthetic-secret#real-fragment",
        json.dumps({"url": "/static/generated/p990030/image%253Fv1.png"}),
        str(root / "p990030/local?#%.mp4"),
        "https://cdn.test/generated/p990030/plain.mp4?signature=synthetic-secret#real-fragment",
    ]}
    await db_session.commit()
    preview = await gc.audit_generated_orphans(db_session, root=root, now=now)
    assert preview["ok"] and preview["candidates"] == [str(orphan)]
    result = await gc.audit_generated_orphans(db_session, root=root, now=now, apply=True)
    assert result["ok"] and result["deleted"] == [str(orphan)]
    assert all(path.exists() for path in retained)
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("database_rollback", [False, True])
async def test_project_delete_never_removes_shared_directory_before_commit(db_session, tmp_path, monkeypatch, database_rollback):
    """真实DELETE成功/历史分镜FK导致回滚都不触碰同号漫剧/旧媒体。"""
    user, project, shot = await seed_project(db_session)
    pid, sid = project.id, shot.id
    db_session.add(DramaProject(id=pid, user_id=user.id, title="共享目录漫剧"))
    if database_rollback:
        task = await make_task(db_session, user, status="succeeded")
        task.project_id, task.shot_id = pid, sid
    await db_session.commit()
    root = tmp_path / "generated"
    media = file_at(root, f"p{pid}/drama-and-old-film.mp4")
    monkeypatch.setattr("app.services.storage.GENERATED_ROOT", root)
    if database_rollback:
        with pytest.raises(IntegrityError):
            await projects.delete_project(pid, db_session, user)
        await db_session.rollback()
        assert await db_session.get(Project, pid)
    else:
        assert (await projects.delete_project(pid, db_session, user))["ok"]
        assert await db_session.get(Project, pid) is None
    assert await db_session.get(DramaProject, pid)
    assert media.read_bytes() == b"preserve unless proven orphan"
