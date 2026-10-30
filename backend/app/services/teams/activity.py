"""与业务变更同事务写入团队动态。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models_teams import TeamActivity


async def record_activity(
    db: AsyncSession, team_id: int, actor_user_id: int, action: str,
    subject: str, project_id: int | None = None,
) -> TeamActivity:
    """追加不含凭据的操作记录，不自行提交调用方事务。"""
    activity = TeamActivity(team_id=team_id, actor_user_id=actor_user_id, action=action[:64], subject=subject[:255], project_id=project_id)
    db.add(activity)
    await db.flush()
    return activity
