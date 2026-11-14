"""历史团队钱包流水只读查询。"""
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_team_billing import TeamWalletLedger
from app.schemas_admin_teams import (
    AdminTeamWalletLedgerOut, AdminTeamWalletLedgerPage,
)
from app.schemas_common import PageMeta
from app.services.teams.admin_queries import require_team


async def list_wallet_ledger(
    db: AsyncSession, team_id: int, *, q: str, adjustments_only: bool, page: int, page_size: int,
) -> AdminTeamWalletLedgerPage:
    """查看全部钱包流水或仅后台调账，按备注/操作者/关联号搜索。"""
    await require_team(db, team_id)
    filters = [TeamWalletLedger.team_id == team_id]
    if adjustments_only:
        filters.append(TeamWalletLedger.ref_type == "admin_adjust")
    if q.strip():
        raw = f"%{q.strip()}%"
        filters.append(or_(TeamWalletLedger.note.ilike(raw), TeamWalletLedger.ref_id.ilike(raw),
                           User.email.ilike(raw), User.nickname.ilike(raw)))
    base = select(TeamWalletLedger, User.nickname, User.email).outerjoin(
        User, User.id == TeamWalletLedger.actor_user_id,
    ).where(*filters)
    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = (await db.execute(base.order_by(TeamWalletLedger.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return AdminTeamWalletLedgerPage(items=[AdminTeamWalletLedgerOut(
        id=row.id, actor_user_id=row.actor_user_id, operator_nickname=nickname or "", operator_email=email or "",
        delta_fen=row.delta_fen, balance_after_fen=row.balance_after_fen, kind=row.kind, ref_type=row.ref_type,
        ref_id=row.ref_id, note=row.note, created_at=row.created_at,
    ) for row, nickname, email in rows], meta=PageMeta(page=page, page_size=page_size, total=total))
