"""平台额度设置沿用团队写锁并记录操作者，不修改任何钱包。"""
from fastapi import HTTPException
from sqlalchemy import select
from app.models_teams import Team
from app.services.teams.activity import record_activity
from app.services.teams.management import active_member


async def set_budget(db, team_id: int, actor_id: int, limit: int | None, *, user_id: int | None = None) -> None:
    """串行化额度设置与入队；降低额度只阻止后续任务。"""
    team = (await db.execute(select(Team).where(Team.id == team_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if team is None:
        raise HTTPException(404, "团队不存在")
    target = team if user_id is None else await active_member(db, team_id, user_id)
    target.monthly_limit_fen = limit
    await record_activity(db, team_id, actor_id, "budget.updated", f"{'团队' if user_id is None else '成员 #' + str(user_id)} 月上限：{'不限额' if limit is None else str(limit) + ' 分'}")
