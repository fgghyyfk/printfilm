"""漫剧审计修复回归：画布不改资产类型、删资产、状态卡死、手写时长与全集重写。"""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.drama import agents, assets, canvas, episodes, projects
from app.models_drama import DramaAsset, DramaEpisode, DramaProject, DramaScript
from app.schemas_drama import DramaCanvasSaveRequest, DramaEpisodeScriptRequest, DramaEpisodeUpdate, DramaProjectUpdate
from app.services.drama import jobs
from app.services.drama.assets_seed_state import (
    SEED_INTERRUPTED_MESSAGE,
    assets_seed_in_progress,
    reconcile_stale_assets_seed,
)
from app.services.drama.build_fragments import prepare_fragment_content, repair_fragment_timed_layout
from app.services.drama.episode_regen import finalize_regen_episodes, regen_working_episodes
from app.services.tasks import executor
from tests.conftest import make_task, make_user

LONG_BODY = "内容" * 2000


async def _project(db, *, params=None):
    """创建只属于本用例的个人项目。"""
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title="回归", params=params or {})
    db.add(project)
    await db.flush()
    return user, project


@pytest.mark.parametrize("asset_type,storage,kind", [("prop", "image", "image"), ("voice", "audio", "image"),
                                                     ("material", "image", "image")])
async def test_canvas_save_keeps_typed_asset(db_session, asset_type, storage, kind):
    """第 1 条：节点按图片卡展示的道具和音色，保存布局后类型不变。"""
    user, project = await _project(db_session)
    asset = DramaAsset(project_id=project.id, type=asset_type, asset_type=storage, name="玉佩")
    db_session.add(asset)
    await db_session.flush()
    node = {"id": f"asset-{asset.id}", "type": "asset", "position": {"x": 1, "y": 2},
            "data": {"assetId": asset.id, "kind": kind, "label": "玉佩"}}
    await canvas.save_canvas(DramaCanvasSaveRequest(project_id=project.id, nodes=[node]), db=db_session, user=user)
    await db_session.refresh(asset)
    assert (asset.type, asset.asset_type) == (asset_type, storage)


async def test_canvas_save_still_types_untyped_asset(db_session):
    """第 1 条：未定型的 none 资产仍可由节点确定类型。"""
    user, project = await _project(db_session)
    asset = DramaAsset(project_id=project.id, type="none", asset_type="image", name="节点")
    db_session.add(asset)
    await db_session.flush()
    node = {"id": "n1", "data": {"assetId": asset.id, "kind": "video", "label": "节点"}}
    await canvas.save_canvas(DramaCanvasSaveRequest(project_id=project.id, nodes=[node]), db=db_session, user=user)
    await db_session.refresh(asset)
    assert (asset.type, asset.asset_type) == ("video", "video")


async def test_delete_generated_asset_keeps_task_history(db_session):
    """第 2 条：生成过的资产可删除（真实外键），任务历史保留并解除引用。"""
    user, project = await _project(db_session)
    asset = DramaAsset(project_id=project.id, type="character", name="角色")
    db_session.add(asset)
    await db_session.flush()
    asset_id = asset.id
    task = await make_task(db_session, user, domain="drama", task_type="asset_image", status="succeeded")
    task.asset_id = asset_id
    await db_session.flush()
    assert await assets.delete_asset(asset_id, db=db_session, user=user) == {"ok": True}
    assert await db_session.get(DramaAsset, asset_id) is None
    await db_session.refresh(task)
    assert task.asset_id is None


async def test_delete_asset_rejected_while_generating(db_session):
    """第 2 条：活动任务还要写回资产时拒绝删除，提示中文短句。"""
    user, project = await _project(db_session)
    asset = DramaAsset(project_id=project.id, type="character", name="角色")
    db_session.add(asset)
    await db_session.flush()
    task = await make_task(db_session, user, domain="drama", task_type="asset_image", status="running")
    task.asset_id = asset.id
    await db_session.flush()
    with pytest.raises(HTTPException) as raised:
        await assets.delete_asset(asset.id, db=db_session, user=user)
    assert raised.value.status_code == 409 and "生成中" in raised.value.detail


async def test_stale_episode_params_cannot_restore_generating(db_session):
    """第 3 条：页面旧快照写回不会把已完成的分镜改回 generating，也不覆盖来源指纹。"""
    user, project = await _project(db_session)
    episode = DramaEpisode(project_id=project.id, name="一", params={
        "episodeNumber": 1, "fragment_plan_status": "completed", "fragment_source_fp": "new", "fragment_plan_task_id": 7,
    })
    db_session.add(episode)
    await db_session.flush()
    stale = {"episodeNumber": 1, "fragment_plan_status": "generating", "fragment_source_fp": "old",
             "fragment_plan_task_id": 3, "fragment_script_changed": True, "subtitleMode": "model"}
    out = await episodes.update_episode(episode.id, DramaEpisodeUpdate(params=stale), db=db_session, user=user)
    assert out.params["fragment_plan_status"] == "completed"
    assert out.params["fragment_source_fp"] == "new"
    assert out.params["fragment_plan_task_id"] == 7
    assert out.params["subtitleMode"] == "model"
    assert "fragment_script_changed" not in out.params


async def test_stale_project_params_cannot_restore_seed_status(db_session):
    """第 4 条：项目设置整包写回不会把抽取状态改回 generating，其他设置正常保存。"""
    user, project = await _project(db_session, params={"assets_seed_status": "done", "linkLastFrame": True})
    body = DramaProjectUpdate(params={"assets_seed_status": "generating", "linkLastFrame": True, "resolution": "720p"})
    out = await projects.update_project(project.id, body, db=db_session, user=user)
    assert out.params["assets_seed_status"] == "done"
    assert out.params["resolution"] == "720p"


async def test_leftover_seed_status_converges_and_unblocks_confirm(db_session):
    """第 4 条：无活动任务的遗留 generating 不算进行中，读取项目时转为失败。"""
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    user, project = await _project(db_session, params={"assets_seed_status": "generating", "assets_seed_generating_at": old})
    assert not await assets_seed_in_progress(db_session, project.id, project.params)
    out = await projects.get_project(project.id, db=db_session, user=user)
    assert out.params["assets_seed_status"] == "failed"
    assert out.params["assets_seed_error"] == SEED_INTERRUPTED_MESSAGE
    assert "assets_seed_generating_at" not in out.params


@pytest.mark.parametrize("task_type", ["seed_assets", "episode_script"])
async def test_live_seed_task_is_not_reconciled(db_session, task_type):
    """第 4 条：抽取任务或带抽取的流水线还活着时，长时间 generating 不能被判失败。"""
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    user, project = await _project(db_session, params={"assets_seed_status": "generating", "assets_seed_generating_at": old})
    task = await make_task(db_session, user, domain="drama", task_type=task_type, status="running")
    task.drama_project_id = project.id
    await db_session.flush()
    assert await assets_seed_in_progress(db_session, project.id, project.params)
    assert await reconcile_stale_assets_seed(db_session, project) is False
    assert project.params["assets_seed_status"] == "generating"


async def test_just_marked_seed_status_gets_admission_grace(db_session):
    """第 4 条：状态刚写入、任务还没建好的窗口内不能被判遗留。"""
    now = datetime.now(UTC).isoformat()
    _, project = await _project(db_session, params={"assets_seed_status": "generating", "assets_seed_generating_at": now})
    assert await assets_seed_in_progress(db_session, project.id, project.params)


def test_manual_duration_beyond_auto_limit_is_preserved():
    """第 7 条：手写 24 秒（Seedance 4–30 内）读取、保存、提交都不被压缩到 15 秒。"""
    content = "@duration:8\n第一段\n@duration:8\n第二段\n@duration:8\n第三段"
    assert prepare_fragment_content(content, duration_sec=24, is_opening=True) == content
    assert prepare_fragment_content(content, duration_sec=15, is_opening=True) == content


def test_manual_duration_over_seedance_limit_is_still_capped():
    """第 7 条：超过 Seedance 上限才合并到 30 秒。"""
    import re
    content = "\n".join(f"@duration:12\n第{i}段" for i in range(4))
    fixed = prepare_fragment_content(content, duration_sec=30, is_opening=True)
    assert sum(int(x) for x in re.findall(r"@duration:(\d+)", fixed)) == 30
    for i in range(4):
        assert f"第{i}段" in fixed


def test_auto_planning_path_keeps_fifteen_second_limit():
    """第 7 条：自动规划不传 manual，仍收紧到 15 秒（EPISODE_RULES §1）。"""
    import re
    content = "@duration:8\n第一段\n@duration:8\n第二段\n@duration:8\n第三段"
    fixed = repair_fragment_timed_layout(content, duration_sec=15)
    assert sum(int(x) for x in re.findall(r"@duration:(\d+)", fixed)) == 15


@pytest.mark.parametrize("settle", ["fail", "before_start", "cancel"])
@pytest.mark.parametrize("has_newer", [False, True])
async def test_plan_task_end_converges_episode_status(db_session, monkeypatch, settle, has_newer):
    """第 9 条：分镜任务失败或取消时分集不再卡在分镜中；新任务已接管时不干扰。"""
    monkeypatch.setattr("app.services.billing.alerts.process_billing_alerts_after_charge", AsyncMock())
    user, project = await _project(db_session)
    episode = DramaEpisode(project_id=project.id, name="一", params={"fragment_plan_status": "generating", "keep": 1})
    db_session.add(episode)
    await db_session.flush()
    task = await make_task(db_session, user, domain="drama", task_type="fragment_plan", status="running")
    task.episode_id = episode.id
    await db_session.flush()
    if has_newer:
        newer = await make_task(db_session, user, domain="drama", task_type="fragment_plan")
        newer.episode_id = episode.id
        await db_session.flush()
    from app.services.tasks.service import get_task_for_runtime
    task = await get_task_for_runtime(db_session, task.id)
    if settle == "fail":
        await executor._fail_task(db_session, task, RuntimeError("落库失败"))
    elif settle == "before_start":
        await executor._fail_task_before_start(db_session, task, None, error_code="x", message="余额不足")
    else:
        await executor._mark_cancelled(db_session, task)
    await db_session.refresh(episode)
    assert episode.params["keep"] == 1
    assert episode.params["fragment_plan_status"] == ("generating" if has_newer else "failed")
    if not has_newer:
        assert episode.params["fragment_plan_error"]


def test_regen_working_copy_keeps_brief_fields_and_clears_body():
    """第 8 条：工作稿去掉正文，保留标题、梗概、备注和手动来源。"""
    old = [{"episodeNumber": 1, "title": "甲", "body": "旧", "summary": "梗概", "creative": "备注", "origin": "manual"}]
    assert regen_working_episodes(old, {}) == [
        {"episodeNumber": 1, "title": "甲", "body": "", "summary": "梗概", "creative": "备注", "origin": "manual"}]
    saved = [{"episodeNumber": 1, "body": "已写好"}]
    assert regen_working_episodes(old, {"episode_regen_draft": saved}) == saved


def test_regen_final_keeps_body_of_episode_not_rewritten():
    """第 8 条：不参与自动重写的手动集，替换时保留原正文。"""
    final = finalize_regen_episodes(
        [{"episodeNumber": 1, "body": "旧一"}, {"episodeNumber": 2, "body": "手写二"}],
        [{"episodeNumber": 1, "body": "新一"}, {"episodeNumber": 2, "body": "", "origin": "manual"}],
    )
    assert [row["body"] for row in final] == ["新一", "手写二"]


@pytest.fixture
def job_session(db_session, monkeypatch):
    """让剧本任务复用测试事务，屏蔽模型、记账和后续分镜。"""
    @asynccontextmanager
    async def session():
        """复用当前测试的保存点事务。"""
        yield db_session
    monkeypatch.setattr(jobs, "AsyncSessionLocal", session)
    monkeypatch.setattr(jobs, "record_line", AsyncMock())
    monkeypatch.setattr(jobs, "record_llm_chat_line", AsyncMock())
    kickoff = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(jobs, "kickoff_fragment_plans", kickoff)
    return SimpleNamespace(db=db_session, kickoff=kickoff)


async def _script_project(db):
    """两集已有正文、梗概和备注的项目。"""
    user, project = await _project(db, params={"episode_count": 2})
    script = DramaScript(
        project_id=project.id, source="原始创意" * 10, summary={"episodeCount": 2, "synopsis": "全剧"},
        params={"episode_count": 2, "episode_content_status": "completed"},
        episode_content={"episodes": [
            {"episodeNumber": n, "title": f"第{n}集名", "body": f"旧正文{n}" + LONG_BODY,
             "summary": f"梗概{n}", "creative": f"备注{n}"} for n in (1, 2)
        ]},
    )
    db.add(script)
    await db.flush()
    # 任务要带上 script 关系重新加载，真实运行时它用的是新会话。
    db.expunge(project)
    return user, project, script


async def test_regen_failure_keeps_old_scripts(job_session, monkeypatch):
    """第 8 条：第二集写稿失败时旧正文、梗概和备注都还在，状态转失败。"""
    db = job_session.db
    _, project, script = await _script_project(db)
    calls = []

    async def batch(summary, existing, **kwargs):
        """第一集成功，第二集模拟模型失败。"""
        number = 1 if not calls else 2
        calls.append(number)
        if number == 2:
            raise RuntimeError("模型失败")
        return [{"episodeNumber": 1, "title": "第1集名", "body": "新正文1" + LONG_BODY}]

    monkeypatch.setattr(jobs, "run_episode_script_batch", batch)
    result = await jobs.run_episode_scripts_job(project.id, force=True)
    assert result["ok"] is False
    await db.refresh(script)
    rows = script.episode_content["episodes"]
    assert [row["body"][:4] for row in rows] == ["旧正文1", "旧正文2"]
    assert [row["summary"] for row in rows] == ["梗概1", "梗概2"]
    assert script.params["episode_content_status"] == "failed"
    assert "episode_regen_draft" not in script.params
    job_session.kickoff.assert_not_called()


async def test_regen_success_replaces_and_keeps_brief(job_session, monkeypatch):
    """第 8 条：全部成功才替换正文，梗概和备注保留，收尾才入队分镜。"""
    db = job_session.db
    _, project, script = await _script_project(db)

    async def batch(summary, existing, **kwargs):
        """按缺失顺序写下一集；写稿期间旧正文不能被改。"""
        current = script.episode_content["episodes"]
        assert all(row["body"].startswith("旧正文") for row in current)
        number = next(int(row["episodeNumber"]) for row in existing if not row["body"])
        return [{"episodeNumber": number, "title": f"第{number}集名", "body": f"新正文{number}" + LONG_BODY}]

    monkeypatch.setattr(jobs, "run_episode_script_batch", batch)
    result = await jobs.run_episode_scripts_job(project.id, force=True)
    assert result["ok"] is True
    await db.refresh(script)
    rows = script.episode_content["episodes"]
    assert [row["body"][:4] for row in rows] == ["新正文1", "新正文2"]
    assert [(row["summary"], row["creative"]) for row in rows] == [("梗概1", "备注1"), ("梗概2", "备注2")]
    assert script.params["episode_content_status"] == "completed"
    assert "episode_regen_draft" not in script.params
    job_session.kickoff.assert_awaited_once()


async def test_force_request_does_not_clear_scripts(db_session, monkeypatch):
    """第 8 条：提交强制重写时接口不清空旧正文，进度从 0 开始。"""
    monkeypatch.setattr(agents, "dispatch_episode_scripts_job", AsyncMock(return_value=99))
    user, project, script = await _script_project(db_session)
    out = await agents.episode_script(
        DramaEpisodeScriptRequest(project_id=project.id, force=True), db=db_session, user=user,
    )
    assert out["total_generated"] == 0
    rows = (await db_session.scalar(select(DramaScript).where(DramaScript.id == script.id))).episode_content["episodes"]
    assert rows[0]["body"].startswith("旧正文1") and rows[0]["summary"] == "梗概1"


async def test_task_foreign_key_really_blocks_plain_asset_delete(db_session):
    """第 2 条前提：不解绑直接删资产，真实外键会拒绝，这正是修复前的 500。"""
    user, project = await _project(db_session)
    asset = DramaAsset(project_id=project.id, type="character", name="角色")
    db_session.add(asset)
    await db_session.flush()
    task = await make_task(db_session, user, domain="drama", task_type="asset_image", status="succeeded")
    task.asset_id = asset.id
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(delete(DramaAsset).where(DramaAsset.id == asset.id))
