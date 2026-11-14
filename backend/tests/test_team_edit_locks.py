"""团队编辑占用与按集保存：仅使用隔离 PostgreSQL 测试事务。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaProject, DramaScript
from tests.conftest import make_user


async def team_project(db: AsyncSession):
    """建立两位协作者和带两集正文的真实团队项目。"""
    from app.models_teams import Team, TeamMember

    owner = await make_user(db)
    member = await make_user(db)
    team = Team(name="占用测试", description="", color="#435c4b", owner_user_id=owner.id)
    db.add(team)
    await db.flush()
    db.add_all([TeamMember(team_id=team.id, user_id=owner.id, role="owner"), TeamMember(team_id=team.id, user_id=member.id, role="member")])
    project = DramaProject(user_id=owner.id, team_id=team.id, title="并行制作")
    db.add(project)
    await db.flush()
    script = DramaScript(project_id=project.id, name="剧本", episode_content={"episodes": [{"episodeNumber": 1, "body": "第一集", "extra": "保留"}, {"episodeNumber": 2, "body": "第二集"}]})
    db.add(script)
    await db.commit()
    return owner, member, team, project


@pytest.mark.asyncio
async def test_team_lock_same_episode_conflicts_and_different_episodes_parallel(db_session):
    from app.services.teams.edit_locks import acquire_lock

    owner, member, team, project = await team_project(db_session)
    first = await acquire_lock(db_session, team.id, owner.id, project.id, "episode", 1, "owner-tab")
    second = await acquire_lock(db_session, team.id, member.id, project.id, "episode", 2, "member-tab")
    assert first["token"] != second["token"]
    with pytest.raises(HTTPException) as exc:
        await acquire_lock(db_session, team.id, member.id, project.id, "episode", 1, "member-tab")
    assert exc.value.status_code == 423
    assert exc.value.detail["owner_user_id"] == owner.id


@pytest.mark.asyncio
async def test_structure_lock_conflicts_with_existing_episode_and_member_cannot_force(db_session):
    from app.services.teams.edit_locks import acquire_lock

    owner, member, team, project = await team_project(db_session)
    await acquire_lock(db_session, team.id, owner.id, project.id, "episode", 1, "owner-tab")
    with pytest.raises(HTTPException) as exc:
        await acquire_lock(db_session, team.id, member.id, project.id, "structure", 0, "member-tab", force=True)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await acquire_lock(db_session, team.id, member.id, project.id, "structure", 0, "member-tab")
    assert exc.value.status_code == 423


@pytest.mark.asyncio
async def test_forced_takeover_invalidates_old_token_and_released_member_cannot_write(db_session):
    from app.models_teams import TeamMember
    from app.services.teams.edit_locks import acquire_lock, validate_lock

    owner, member, team, project = await team_project(db_session)
    old = await acquire_lock(db_session, team.id, member.id, project.id, "asset", 0, "member-tab")
    new = await acquire_lock(db_session, team.id, owner.id, project.id, "asset", 0, "owner-tab", force=True)
    assert new["version"] > old["version"]
    with pytest.raises(HTTPException):
        await validate_lock(db_session, project, member.id, "asset", 0, "member-tab", old["token"], old["version"])
    membership = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == member.id))).scalar_one()
    membership.active = False
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await acquire_lock(db_session, team.id, member.id, project.id, "canvas", 0, "member-tab")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_episode_patch_preserves_other_episode_extra_fields_and_explicit_empty(db_session):
    from app.services.teams.script_updates import patch_episode_body

    owner, member, team, project = await team_project(db_session)
    await patch_episode_body(db_session, project.id, 1, {"body": ""})
    await patch_episode_body(db_session, project.id, 2, {"body": "第二集修改", "creative": "创意"})
    await db_session.commit()
    from sqlalchemy import select

    script = (await db_session.execute(select(DramaScript).where(DramaScript.project_id == project.id).execution_options(populate_existing=True))).scalar_one()
    assert script.episode_content["episodes"][0] == {"episodeNumber": 1, "body": "", "extra": "保留"}
    assert script.episode_content["episodes"][1]["body"] == "第二集修改"
