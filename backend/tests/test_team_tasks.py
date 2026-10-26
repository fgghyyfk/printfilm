"""Team task scope is resolved from persisted resources and current membership."""
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models_drama import DramaAsset, DramaProject
from app.models_tasks import TaskRun
from app.models_teams import TeamMember
from app.schemas_tasks import TaskCreateRequest
from app.services.tasks.service import _validate_task_scope, cancel_task_for_user, get_task_for_user, list_tasks_for_user
from test_team_billing import seed_team, enqueue


@pytest.mark.asyncio
async def test_task_derives_team_from_asset_and_rejects_mixed_scope(db_session):
    team, owner, member = await seed_team(db_session)
    project = DramaProject(user_id=owner.id, team_id=team.id, title="Team")
    db_session.add(project)
    await db_session.flush()
    asset = DramaAsset(project_id=project.id, name="角色", type="character")
    db_session.add(asset)
    await db_session.flush()
    body = TaskCreateRequest(task_type="asset_image", asset_id=asset.id)
    await _validate_task_scope(db_session, member, body)
    assert (body.team_id, body.drama_project_id) == (team.id, project.id)
    with pytest.raises(ValueError, match="团队不匹配"):
        await _validate_task_scope(db_session, member, TaskCreateRequest(task_type="asset_image", asset_id=asset.id, team_id=team.id + 1))
    personal = DramaProject(user_id=member.id, title="Personal")
    db_session.add(personal)
    await db_session.flush()
    with pytest.raises(ValueError, match="不匹配"):
        await _validate_task_scope(db_session, member, TaskCreateRequest(task_type="asset_image", asset_id=asset.id, drama_project_id=personal.id))


@pytest.mark.asyncio
async def test_team_task_read_and_cancel_roles_and_personal_list(db_session, monkeypatch):
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    assert (await get_task_for_user(db_session, owner, task.id)).id == task.id
    rows, count = await list_tasks_for_user(db_session, member, page=1, page_size=20)
    assert count == 0
    rows, count = await list_tasks_for_user(db_session, owner, team_id=team.id, page=1, page_size=20)
    assert count == 1
    other = await enqueue(db_session, owner, team, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await cancel_task_for_user(db_session, member, other.id)
    assert exc.value.status_code == 403
    assert (await cancel_task_for_user(db_session, owner, task.id)).cancel_requested


@pytest.mark.asyncio
async def test_removed_member_loses_access_but_running_task_keeps_original_wallet(db_session, monkeypatch):
    from app.services.tasks.team_scope import cancel_removed_member_tasks, ensure_task_may_submit
    from app.models_team_billing import TeamMonthlyUsage
    team, owner, member = await seed_team(db_session)
    queued = await enqueue(db_session, member, team, monkeypatch)
    submitted = await enqueue(db_session, member, team, monkeypatch)
    submitted.provider_task_id = "already-submitted"
    submitted.status = "awaiting_poll"
    membership = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == member.id))).scalar_one()
    membership.active = False
    assert await cancel_removed_member_tasks(db_session, team.id, member.id) == 1
    assert queued.status == "cancelled"
    assert submitted.status == "awaiting_poll"
    assert not submitted.cancel_requested
    with pytest.raises(HTTPException):
        await get_task_for_user(db_session, member, submitted.id)
    with pytest.raises(HTTPException):
        await ensure_task_may_submit(db_session, submitted)
    usage = (await db_session.execute(select(TeamMonthlyUsage).where(TeamMonthlyUsage.user_id == member.id))).scalar_one()
    assert usage.reserved_fen == 300



@pytest.mark.asyncio
async def test_removed_team_member_cannot_submit_but_can_finish_polling(db_session, monkeypatch):
    from types import SimpleNamespace
    import httpx
    from app.services.billing.context import billing_scope
    from app.services.tasks import upstream
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    membership = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == member.id))).scalar_one()
    membership.active = False
    await db_session.flush()
    monkeypatch.setattr(upstream, "_safe_record", AsyncMock())
    client = SimpleNamespace(post=AsyncMock(), get=AsyncMock(return_value=httpx.Response(200, json={"status": "succeeded"})))
    async with billing_scope(task.id, team_id=team.id), upstream.upstream_event_session(db_session):
        with pytest.raises(HTTPException):
            await upstream.observed_request(client, "POST", "https://stub.test/v1/videos", json={"model": "stub"})
        await upstream.observed_request(client, "GET", "https://stub.test/v1/videos/submitted")
    client.post.assert_not_awaited()
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_public_task_projection_hides_upstream_details(db_session, monkeypatch):
    from app.api.tasks import public_task
    from app.models_tasks import TaskEvent, TaskStep
    team, owner, member = await seed_team(db_session)
    task = await enqueue(db_session, member, team, monkeypatch)
    db_session.add_all([
        TaskEvent(task_id=task.id, event_type="upstream.request.accepted", payload={"cost_fen": 123}),
        TaskEvent(task_id=task.id, event_type="task.started", message="开始"),
        TaskStep(task_id=task.id, step_key="asset_image", input_payload={"provider": "internal"}, output_payload={"cost": 123}),
    ])
    await db_session.flush()
    loaded = await get_task_for_user(db_session, owner, task.id)
    output = public_task(loaded)
    assert [event.event_type for event in output.events] == ["task.started"]
    assert output.steps[0].input_payload is None
    assert output.steps[0].output_payload is None
    assert len(loaded.events) == 2
    assert loaded.steps[0].output_payload == {"cost": 123}


@pytest.mark.asyncio
async def test_background_child_inherits_team_actor_and_cannot_forge_scope(db_session, monkeypatch):
    from app.services.billing.context import billing_scope
    from app.services.tasks.service import create_task
    team, owner, member = await seed_team(db_session)
    project = DramaProject(user_id=owner.id, team_id=team.id, title="Team pipeline")
    db_session.add(project)
    await db_session.flush()
    parent = TaskRun(domain="drama", task_type="script_summary", status="running", team_id=team.id, drama_project_id=project.id, requested_by=member.id, payload={"_team_edit_resource": ["structure", 0]})
    db_session.add(parent)
    await db_session.flush()
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=100))
    async with billing_scope(parent.id, team_id=team.id):
        child = await create_task(db_session, owner, TaskCreateRequest(task_type="episode_script", drama_project_id=project.id, payload={"episode_number": 1, "_team_edit_resource": ["asset", 999999]}), commit=False)
    assert child.requested_by == member.id
    assert child.team_id == team.id
    assert child.payload["_team_edit_resource"] == ["episode", 1]
    assert child.payload.get("_team_parent_task_id") == parent.id


@pytest.mark.asyncio
async def test_postgres_project_transfer_and_task_admission_are_serialized(db_session, monkeypatch):
    import asyncio
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.config import get_settings
    from app.models import User
    from app.models_tasks import TaskEvent, TaskStep, TaskTarget
    from app.models_teams import Team, TeamActivity
    from app.models_team_billing import TeamMonthlyUsage, TeamWallet, TeamWalletLedger
    from app.services.tasks.service import create_task
    from app.services.teams.content import transfer_project
    from conftest import _resolve_test_database_url
    url, _ = _resolve_test_database_url(get_settings().database_url)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    team_id, project_id, second_id = None, None, None
    user_ids = []
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=100))
    try:
        async with sessions() as db:
            team, owner, member = await seed_team(db)
            first = DramaProject(user_id=owner.id, title="Task wins")
            second = DramaProject(user_id=owner.id, title="Transfer wins")
            db.add_all([first, second])
            await db.flush()
            team_id, project_id, second_id = team.id, first.id, second.id
            user_ids = [owner.id, member.id]
            owner_id = owner.id
            await db.commit()
        async with sessions() as task_db, sessions() as transfer_db:
            actor = await task_db.get(User, owner_id)
            queued = await create_task(task_db, actor, TaskCreateRequest(task_type="script_summary", drama_project_id=project_id), commit=False)
            assert queued.team_id is None
            attempted = asyncio.Event()
            original_execute = transfer_db.execute
            async def transfer_execute(statement, *args, **kwargs):
                if getattr(statement, "_for_update_arg", None) is not None and "drama_projects" in str(statement):
                    attempted.set()
                return await original_execute(statement, *args, **kwargs)
            monkeypatch.setattr(transfer_db, "execute", transfer_execute)
            async def transfer():
                with pytest.raises(HTTPException) as exc:
                    await transfer_project(transfer_db, team_id, owner_id, project_id)
                assert exc.value.status_code == 409
                await transfer_db.rollback()
            waiting = asyncio.create_task(transfer())
            await asyncio.wait_for(attempted.wait(), timeout=3)
            await task_db.commit()
            await asyncio.wait_for(waiting, timeout=3)
        async with sessions() as task_db, sessions() as transfer_db:
            actor = await task_db.get(User, owner_id)
            # Keep an old personal-space identity in this session, then let transfer win.
            await task_db.get(DramaProject, second_id)
            transferred, copier = await transfer_project(transfer_db, team_id, owner_id, second_id)
            attempted = asyncio.Event()
            original_execute = task_db.execute
            async def task_execute(statement, *args, **kwargs):
                if getattr(statement, "_for_update_arg", None) is not None and "drama_projects" in str(statement):
                    attempted.set()
                return await original_execute(statement, *args, **kwargs)
            monkeypatch.setattr(task_db, "execute", task_execute)
            async def submit_stale():
                with pytest.raises(ValueError, match="归属已变化"):
                    await create_task(task_db, actor, TaskCreateRequest(task_type="script_summary", drama_project_id=second_id), commit=False)
                await task_db.rollback()
            waiting = asyncio.create_task(submit_stale())
            await asyncio.wait_for(attempted.wait(), timeout=3)
            await transfer_db.commit()
            await asyncio.wait_for(waiting, timeout=3)
            assert transferred.team_id == team_id
    finally:
        if team_id is not None:
            async with sessions() as db:
                task_ids = select(TaskRun.id).where(TaskRun.drama_project_id.in_([project_id, second_id]))
                for model in (TaskEvent, TaskStep, TaskTarget):
                    await db.execute(delete(model).where(model.task_id.in_(task_ids)))
                await db.execute(delete(TaskRun).where(TaskRun.drama_project_id.in_([project_id, second_id])))
                await db.execute(delete(TeamActivity).where(TeamActivity.team_id == team_id))
                await db.execute(delete(DramaProject).where(DramaProject.id.in_([project_id, second_id])))
                for model in (TeamMonthlyUsage, TeamWalletLedger, TeamWallet, TeamMember):
                    await db.execute(delete(model).where(model.team_id == team_id))
                await db.execute(delete(Team).where(Team.id == team_id))
                from app.models import WalletLedger
                await db.execute(delete(WalletLedger).where(WalletLedger.user_id.in_(user_ids)))
                await db.execute(delete(User).where(User.id.in_(user_ids)))
                await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("succeed", [True, False])
async def test_team_ephemeral_releases_admission_locks_and_finishes_durably(db_session, monkeypatch, succeed):
    """No shared admission lock spans the executor, including commit=False callers."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.config import get_settings
    from app.models import User
    from app.models_tasks import TaskEvent, TaskStep
    from app.models_teams import Team
    from app.models_team_billing import TeamMonthlyUsage, TeamWallet, TeamWalletLedger
    from app.services.billing.ephemeral import run_billed_ephemeral
    from conftest import _resolve_test_database_url
    url, _ = _resolve_test_database_url(get_settings().database_url)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    team_id, project_id = None, None
    user_ids = []
    monkeypatch.setattr("app.services.billing.settlement.estimate_task_fen", AsyncMock(return_value=100))
    try:
        async with sessions() as db:
            team, owner, member = await seed_team(db)
            project = DramaProject(user_id=owner.id, team_id=team.id, title="Inline task")
            db.add(project)
            await db.flush()
            team_id, project_id, member_id = team.id, project.id, member.id
            user_ids = [owner.id, member.id]
            await db.commit()
        async with sessions() as db:
            actor = await db.get(User, member_id)
            async def executor():
                async with sessions() as check_db:
                    # NOWAIT is a real PostgreSQL assertion: no lease/wallet lock is
                    # allowed to remain held by the network task's admission phase.
                    for model, filters in (
                        (Team, [Team.id == team_id]),
                        (TeamMember, [TeamMember.team_id == team_id, TeamMember.user_id == member_id]),
                        (DramaProject, [DramaProject.id == project_id]),
                        (TeamWallet, [TeamWallet.team_id == team_id]),
                    ):
                        assert (await check_db.execute(select(model).where(*filters).with_for_update(nowait=True))).scalar_one() is not None
                    running = (await check_db.execute(select(TaskRun).where(TaskRun.drama_project_id == project_id))).scalar_one()
                    assert running.status == "running"
                if not succeed:
                    raise RuntimeError("stub upstream failure")
                return {"ok": True}
            if succeed:
                await run_billed_ephemeral(db, actor, domain="drama", task_type="agent_chat", drama_project_id=project_id, executor=executor, commit=False)
            else:
                with pytest.raises(RuntimeError, match="stub upstream failure"):
                    await run_billed_ephemeral(db, actor, domain="drama", task_type="agent_chat", drama_project_id=project_id, executor=executor, commit=False)
            # A later caller rollback must not revive an accepted running task or freeze.
            await db.rollback()
        async with sessions() as db:
            finished = (await db.execute(select(TaskRun).where(TaskRun.drama_project_id == project_id))).scalar_one()
            assert finished.status == ("succeeded" if succeed else "failed")
            assert finished.billing_status == "settled"
            wallet = await db.get(TeamWallet, team_id)
            assert (wallet.balance_fen, wallet.frozen_fen) == (0, 0)
    finally:
        if team_id is not None:
            async with sessions() as db:
                ids = select(TaskRun.id).where(TaskRun.drama_project_id == project_id)
                for model in (TaskEvent, TaskStep):
                    await db.execute(delete(model).where(model.task_id.in_(ids)))
                await db.execute(delete(TaskRun).where(TaskRun.drama_project_id == project_id))
                await db.execute(delete(DramaProject).where(DramaProject.id == project_id))
                for model in (TeamMonthlyUsage, TeamWalletLedger, TeamWallet, TeamMember):
                    await db.execute(delete(model).where(model.team_id == team_id))
                await db.execute(delete(Team).where(Team.id == team_id))
                from app.models import WalletLedger
                await db.execute(delete(WalletLedger).where(WalletLedger.user_id.in_(user_ids)))
                await db.execute(delete(User).where(User.id.in_(user_ids)))
                await db.commit()
        await engine.dispose()
