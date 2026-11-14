# -*- coding: utf-8 -*-
"""额度告警：用户消费里程碑弹窗。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import BillingAlertNotification, UsageEvent, User


def _fen_to_yuan(fen: int) -> str:
    return f"¥{int(fen) / 100:.2f}"


async def _user_total_charge_fen(db: AsyncSession, user_id: int) -> int:
    row = (
        await db.execute(
            select(func.coalesce(func.sum(UsageEvent.charge_fen), 0)).where(
                func.coalesce(UsageEvent.billing_payer_user_id, UsageEvent.user_id) == int(user_id),
                UsageEvent.settled.is_(True),
            )
        )
    ).scalar_one()
    return int(row or 0)


async def process_user_milestone_alert(
    db: AsyncSession,
    user: User,
    *,
    settings: Settings | None = None,
) -> list[BillingAlertNotification]:
    """结算后检查用户累计扣费是否跨越里程碑，写入待弹窗通知。

    每笔结算最多创建 1 条（跳到已跨过的最高档），避免一次连弹多窗。
    """
    s = settings or get_settings()
    if not s.billing_user_alert_enabled:
        return []
    interval = int(s.billing_user_alert_interval_fen or 0)
    if interval <= 0:
        return []

    total = await _user_total_charge_fen(db, int(user.id))
    last = int(getattr(user, "billing_alert_last_milestone_fen", 0) or 0)
    target = (total // interval) * interval
    if target <= last:
        return []

    note = BillingAlertNotification(
        user_id=int(user.id),
        kind="user_milestone",
        title="消费提醒",
        message=(
            f"您已累计消费 {_fen_to_yuan(target)}。"
            f"当前累计扣费 {_fen_to_yuan(total)}，请注意账户余额。"
        ),
        milestone_fen=target,
    )
    db.add(note)
    user.billing_alert_last_milestone_fen = target
    await db.flush()
    return [note]


async def process_billing_alerts_after_charge(
    db: AsyncSession,
    user: User | None,
    *,
    charged_fen: int,
    settings: Settings | None = None,
) -> None:
    """结算产生扣费后触发用户消费提醒。"""
    if int(charged_fen or 0) <= 0:
        return
    s = settings or get_settings()
    if user is not None:
        await process_user_milestone_alert(db, user, settings=s)


async def list_pending_user_alerts(db: AsyncSession, user_id: int) -> list[BillingAlertNotification]:
    rows = list(
        (
            await db.execute(
                select(BillingAlertNotification)
                .where(
                    BillingAlertNotification.user_id == int(user_id),
                    BillingAlertNotification.acknowledged.is_(False),
                )
                .order_by(BillingAlertNotification.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if len(rows) <= 1:
        return rows
    # 历史按 ¥10 档积压时：只保留最新一条，其余自动确认，避免连点刷屏
    for old in rows[:-1]:
        old.acknowledged = True
    await db.flush()
    return [rows[-1]]


async def acknowledge_user_alert(db: AsyncSession, user_id: int, alert_id: int) -> bool:
    row = await db.get(BillingAlertNotification, int(alert_id))
    if not row or int(row.user_id) != int(user_id):
        return False
    row.acknowledged = True
    await db.flush()
    return True
