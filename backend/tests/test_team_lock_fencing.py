"""无需数据库的系统派生范围与内容版本安全回归。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models_drama import DramaScript
from app.models_tasks import TaskRun
from app.services.billing.context import billing_scope
from app.services.teams import edit_locks
from app.services.teams.script_updates import bump_episode_versions, patch_episode_body


@pytest.mark.asyncio
async def test_child_task_cannot_use_stale_human_snapshot_to_escape_parent(monkeypatch):
    """父任务第1集的授权不能因人工会话残留而放行真实第2集子任务。"""
    parent = TaskRun(id=41, domain="drama", task_type="episode_script", requested_by=7, team_id=3, drama_project_id=11, status="running", payload={"episode_number": 1})
    child = TaskRun(domain="drama", task_type="episode_script", requested_by=7, team_id=3, drama_project_id=11, payload={"episode_number": 2})
    db = SimpleNamespace(info={"team_parent_task_id": 41, "team_edit_project_id": 11, "team_edit_resource": ("episode", 1)}, get=AsyncMock(return_value=parent))
    monkeypatch.setattr(edit_locks, "_project", AsyncMock(return_value=SimpleNamespace(id=11)))
    async with billing_scope(parent.id, team_id=3):
        with pytest.raises(HTTPException) as error:
            await edit_locks.claim_task_locks(db, child, 7)
    assert error.value.status_code == 409
    assert "_team_edit_resource" not in child.payload


@pytest.mark.asyncio
async def test_human_snapshot_must_cover_actual_task_target(monkeypatch):
    """任务真实目标与已验证请求不一致时，即使相同项目也必须拒绝。"""
    child = TaskRun(domain="drama", task_type="episode_script", requested_by=7, team_id=3, drama_project_id=11, payload={"episode_number": 2})
    db = SimpleNamespace(info={"team_edit_project_id": 11, "team_edit_resource": ("episode", 1)})
    monkeypatch.setattr(edit_locks, "_project", AsyncMock(return_value=SimpleNamespace(id=11)))
    with pytest.raises(HTTPException) as error:
        await edit_locks.claim_task_locks(db, child, 7)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_structural_parent_grants_child_only_its_real_scope(monkeypatch):
    """整剧任务可派生本集任务，但持久占用缩小到真实集号。"""
    parent = TaskRun(id=41, domain="drama", task_type="script_summary", requested_by=7, team_id=3, drama_project_id=11, status="running", payload={})
    child = TaskRun(domain="drama", task_type="episode_script", requested_by=7, team_id=3, drama_project_id=11, payload={"episode_number": 2})
    db = SimpleNamespace(info={"team_parent_task_id": 41}, get=AsyncMock(return_value=parent))
    monkeypatch.setattr(edit_locks, "_project", AsyncMock(return_value=SimpleNamespace(id=11)))
    async with billing_scope(parent.id, team_id=3):
        await edit_locks.claim_task_locks(db, child, 7)
    assert child.payload["_team_edit_resource"] == ["episode", 2]
    assert child.payload["_team_parent_task_id"] == parent.id


@pytest.mark.asyncio
async def test_generated_content_invalidates_older_editor_version(monkeypatch):
    """模型完成后旧草稿保存必须冲突，其他集的内容与版本保持不变。"""
    script = DramaScript(project_id=11, name="剧本", episode_content={"episodes": [{"episodeNumber": 1, "body": "模型新正文"}, {"episodeNumber": 2, "body": "第二集"}]}, params={"episode_versions": {"1": 3, "2": 8}})
    bump_episode_versions(script, [1])
    monkeypatch.setattr("app.services.teams.script_updates.locked_script", AsyncMock(return_value=script))
    with pytest.raises(HTTPException) as error:
        await patch_episode_body(SimpleNamespace(flush=AsyncMock()), 11, 1, {"version": 3, "body": "旧草稿"})
    assert error.value.status_code == 409
    assert script.params["episode_versions"] == {"1": 4, "2": 8}
    assert script.episode_content["episodes"][0]["body"] == "模型新正文"


@pytest.mark.asyncio
async def test_asset_copy_keeps_content_but_drops_source_binding_ids():
    """副本音色可独立使用，不继续依赖来源角色或音色资产 ID。"""
    from app.services.teams.content_media import MediaCopier

    source = {"description": "角色说明", "voiceAudio": {"sourceAssetId": 8, "speaker": "voice-a"}, "sourceCharacterAssetId": 9, "canvas": {"id": "old"}, "image_versions": [{"id": "v1", "label": "定妆"}]}
    result = await MediaCopier(2, {1, 2}).rewrite(source, clean=True)
    assert result == {"description": "角色说明", "voiceAudio": {"speaker": "voice-a"}, "image_versions": [{"id": "v1", "label": "定妆"}]}
    assert source["voiceAudio"]["sourceAssetId"] == 8
