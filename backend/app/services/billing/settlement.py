# -*- coding: utf-8 -*-
"""任务级预扣与结算。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import UsageEvent, User, WalletLedger
from app.models_tasks import TaskRun
from app.services.public_errors import InsufficientBalanceError
from app.services.billing.estimates import estimate_task_fen, is_free_local_task

logger = logging.getLogger(__name__)

# 仍占用余额承诺、尚未预扣冻结的进行中任务
_PENDING_BILLING_TASK_STATUSES = frozenset(
    {
        "pending",
        "leased",
        "running",
        "awaiting_poll",
        "awaiting_review",
        "cancel_requested",
    }
)


@dataclass
class PreparedTaskSettlements:
    """本次分镜变更的锁外日志快照及提交后管理员检查标记。"""
    newapi_logs: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    event_ids: dict[int, set[int]] = field(default_factory=dict)
    check_admin_after_commit: bool = False


class PreparedSettlementChanged(RuntimeError):
    """结算实际读取的已有用量超出锁外快照，本次编辑必须回滚。"""


def _require_prepared_events(
    prepared: PreparedTaskSettlements | None, task_id: int, events: list[UsageEvent],
) -> None:
    """以结算实际读点为界拒绝未覆盖用量，读取之后的迟到成本由任务补偿单独处理。"""
    if prepared is not None and not {int(event.id) for event in events} <= prepared.event_ids.get(task_id, set()):
        raise PreparedSettlementChanged("内容已在别处更新，请刷新")


async def finish_prepared_task_settlements(db: AsyncSession, prepared: PreparedTaskSettlements) -> None:
    """业务提交成功后才在独立小事务检查管理员邮件；失败不反转业务结果。"""
    if not prepared.check_admin_after_commit:
        return
    prepared.check_admin_after_commit = False
    from app.services.billing.alerts import process_admin_cost_alert

    try:
        async with AsyncSession(bind=db.bind, expire_on_commit=False) as alert_db:
            await process_admin_cost_alert(alert_db)
            await alert_db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("分镜变更已提交，管理员费用提醒检查失败")


def billing_active(user: User | None = None, settings: Settings | None = None) -> bool:
    """全局计费开关；user 参数保留兼容调用方，不再按用户跳过扣费。"""
    _ = user
    s = settings or get_settings()
    return bool(s.billing_enabled)


async def _ledger(
    db: AsyncSession,
    user: User,
    delta_fen: int,
    kind: str,
    *,
    ref_type: str = "",
    ref_id: str = "",
    note: str = "",
) -> None:
    user.balance_fen = int(user.balance_fen or 0) + int(delta_fen)
    db.add(
        WalletLedger(
            user_id=user.id,
            delta_fen=int(delta_fen),
            balance_after=int(user.balance_fen),
            kind=kind,
            ref_type=ref_type,
            ref_id=ref_id,
            note=note[:255],
        )
    )


async def _lock_user(db: AsyncSession, user_id: int) -> User | None:
    """行锁用户钱包，避免并发预扣/结算透支；populate_existing 防止会话内过期余额。"""
    return (
        await db.execute(
            select(User)
            .where(User.id == int(user_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _lock_task(db: AsyncSession, task_id: int) -> TaskRun | None:
    """行锁 TaskRun，保证结算/预扣幂等；populate_existing 防止会话内过期 billing_status。"""
    return (
        await db.execute(
            select(TaskRun)
            .where(TaskRun.id == int(task_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def pending_task_commitment_fen(
    db: AsyncSession,
    user_id: int,
    *,
    exclude_task_id: int | None = None,
) -> int:
    """汇总用户进行中、尚未预扣冻结的任务估算占用（分）。"""
    stmt = select(TaskRun).where(
        TaskRun.requested_by == int(user_id),
        TaskRun.billing_status == "none",
        TaskRun.status.in_(_PENDING_BILLING_TASK_STATUSES),
    )
    if exclude_task_id is not None:
        stmt = stmt.where(TaskRun.id != int(exclude_task_id))
    rows = list((await db.execute(stmt)).scalars().all())
    total = 0
    for row in rows:
        est = int(row.billing_estimate_fen or 0)
        if est <= 0:
            est = await estimate_task_fen(db, row)
        total += est
    return total


async def ensure_balance_for_task(
    db: AsyncSession, user: User, task: TaskRun, *, prepared_estimate_fen: int | None = None,
) -> int:
    """入队前同步校验余额（含排队中未冻结估算），不足抛 ValueError；返回本任务估算分。"""
    if is_free_local_task(task) or not billing_active(user):
        return 0
    locked = await _lock_user(db, int(user.id))
    if locked is None:
        raise ValueError("用户不存在")
    # 同步调用方持有的 user 对象余额
    user.balance_fen = int(locked.balance_fen or 0)
    user.frozen_fen = int(locked.frozen_fen or 0)
    need = (int(prepared_estimate_fen) if prepared_estimate_fen is not None
            else await estimate_task_fen(db, task))
    pending = await pending_task_commitment_fen(db, int(user.id))
    available = int(locked.balance_fen or 0)
    required = pending + need
    if available < required:
        if pending > 0:
            raise InsufficientBalanceError(
                f"余额不足：本次需要 ¥{need/100:.2f}（含排队中 ¥{pending/100:.2f}），"
                f"当前 ¥{available/100:.2f}，请先充值"
            )
        raise InsufficientBalanceError(f"余额不足：需要 ¥{need/100:.2f}，当前 ¥{available/100:.2f}，请先充值")
    return need


async def ensure_balance_for_task_batch(
    db: AsyncSession,
    user: User,
    task: TaskRun,
    count: int,
) -> dict[str, int]:
    """批量入队前校验：pending + unit*count。不足抛 ValueError。"""
    if not billing_active(user):
        return {"unit_estimate_fen": 0, "required_total_fen": 0}
    qty = max(1, int(count))
    locked = await _lock_user(db, int(user.id))
    if locked is None:
        raise ValueError("用户不存在")
    user.balance_fen = int(locked.balance_fen or 0)
    user.frozen_fen = int(locked.frozen_fen or 0)
    unit = await estimate_task_fen(db, task)
    pending = await pending_task_commitment_fen(db, int(user.id))
    additional = unit * qty
    required = pending + additional
    available = int(locked.balance_fen or 0)
    if available < required:
        raise InsufficientBalanceError(
            f"余额不足：批量生成 {qty} 项需 ¥{additional/100:.2f}"
            f"（含排队中 ¥{pending/100:.2f}），当前 ¥{available/100:.2f}，请先充值"
        )
    return {
        "unit_estimate_fen": unit,
        "pending_commitment_fen": pending,
        "requested_total_fen": additional,
        "required_total_fen": required,
        "balance_fen": available,
    }


async def freeze_for_task(db: AsyncSession, task: TaskRun) -> int:
    """任务开始前预扣估算；余额不足抛 ValueError。已 frozen 时幂等返回原估算。"""
    locked_task = await _lock_task(db, int(task.id))
    if not locked_task:
        return 0
    # 回写到调用方持有的 task 引用
    if locked_task is not task:
        task.billing_status = locked_task.billing_status
        task.billing_estimate_fen = locked_task.billing_estimate_fen
    if task.billing_status == "frozen":
        return int(task.billing_estimate_fen or 0)

    if is_free_local_task(task):
        task.billing_status = "skipped"
        task.billing_estimate_fen = 0
        await db.flush()
        return 0
    user = await _lock_user(db, int(task.requested_by))
    if not user or not billing_active(user):
        task.billing_status = "skipped"
        task.billing_estimate_fen = 0
        await db.flush()
        return 0
    need = await estimate_task_fen(db, task)
    available = int(user.balance_fen or 0)
    if available < need:
        raise InsufficientBalanceError(f"余额不足：需要 ¥{need/100:.2f}，当前 ¥{available/100:.2f}，请先充值")
    # 仅通过 _ledger 扣余额，避免双重扣款
    user.frozen_fen = int(user.frozen_fen or 0) + need
    task.billing_estimate_fen = need
    task.billing_status = "frozen"
    await _ledger(
        db,
        user,
        -need,
        "freeze",
        ref_type="task_run",
        ref_id=str(task.id),
        note=f"freeze:{task.domain}/{task.task_type}",
    )
    await db.flush()
    return need


async def settle_task(
    db: AsyncSession, task_id: int, *, prepared_settlements: PreparedTaskSettlements | None = None,
) -> dict[str, int]:
    """任务结束时按 usage_events 结算，多退少补冻结额。"""
    task = await _lock_task(db, task_id)
    if not task:
        return {"charged": 0, "refunded": 0}
    if task.billing_status == "settled":
        return {
            "charged": int(task.billing_charged_fen or 0),
            "refunded": int(task.billing_refunded_fen or 0),
        }
    from app.services.video_recovery import video_cost_unconfirmed

    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    # HTTP内音色仍在执行，或视频提交/结果未知：本地取消不代表上游停止/免费。
    if result.get("voice_billing_reservation") or video_cost_unconfirmed(task):
        task.status = "awaiting_review"
        task.error_code = "billing_review_pending"
        task.error_message = "上游任务结果和费用待核对，已保留冻结金额，请勿重新生成"
        task.finished_at = None
        task.next_action_at = None
        task.lease_until = None
        task.current_step_status = "awaiting_review"
        await db.flush()
        return {"charged": 0, "refunded": 0}
    if isinstance(task.result_payload, dict) and task.result_payload.get("tts_billing_pending"):
        # 已成功的付费配音尚未落账；取消、失败补偿或直接结算都不能当成零成本退款。
        task.status = "awaiting_review"
        task.error_code = "tts_billing_pending"
        task.error_message = "配音已生成，计费记录待核对，请勿重新生成"
        task.finished_at = None
        task.next_action_at = None
        task.lease_until = None
        task.current_step_status = "awaiting_review"
        task.progress_percent = min(int(task.progress_percent or 0), 99)
        await db.flush()
        return {"charged": 0, "refunded": 0}


    # 钱包侧幂等：已有 unfreeze/settle 流水则只对齐状态，禁止二次退还
    if task.billing_status == "frozen":
        prior = (
            await db.execute(
                select(WalletLedger.id)
                .where(
                    WalletLedger.ref_type == "task_run",
                    WalletLedger.ref_id == str(task_id),
                    WalletLedger.kind.in_(("unfreeze", "settle")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if prior is not None:
            result = await db.execute(
                select(UsageEvent).where(
                    UsageEvent.task_run_id == task_id,
                    UsageEvent.settled.is_(False),
                )
            )
            prior_events = list(result.scalars().all())
            _require_prepared_events(prepared_settlements, task_id, prior_events)
            for e in prior_events:
                e.settled = True
            task.billing_status = "settled"
            await db.flush()
            return {
                "charged": int(task.billing_charged_fen or 0),
                "refunded": int(task.billing_refunded_fen or 0),
            }

    result = await db.execute(
        select(UsageEvent).where(
            UsageEvent.task_run_id == task_id,
            UsageEvent.settled.is_(False),
        )
    )
    events = list(result.scalars().all())
    _require_prepared_events(prepared_settlements, task_id, events)
    if events:
        try:
            from app.services.billing.newapi_bill import apply_newapi_bills

            if prepared_settlements is None:
                await apply_newapi_bills(events)
            else:
                await apply_newapi_bills(events, logs=prepared_settlements.newapi_logs.get(task_id, []))
        except Exception:  # noqa: BLE001
            logger.exception("New API 账单对账失败，沿用已记录费用 task=%s", task_id)
    # 用量已结但状态仍 frozen（异常中断）：按已落账实扣对齐，禁止把整笔预扣当退款
    if (
        task.billing_status == "frozen"
        and not events
        and int(task.billing_charged_fen or 0) > 0
    ):
        task.billing_status = "settled"
        await db.flush()
        return {
            "charged": int(task.billing_charged_fen or 0),
            "refunded": int(task.billing_refunded_fen or 0),
        }
    if task.billing_status == "skipped" and not events:
        # 全局关闭计费且无用量：结算完成，统一标 settled 便于管理端展示
        task.billing_status = "settled"
        task.billing_charged_fen = int(task.billing_charged_fen or 0)
        await db.flush()
        return {
            "charged": int(task.billing_charged_fen or 0),
            "refunded": 0,
        }

    charged = sum(int(e.charge_fen or 0) for e in events)
    for e in events:
        e.settled = True

    # 免计费任务：标记用量已结算，不动钱包，任务标 settled
    if task.billing_status == "skipped":
        task.billing_charged_fen = charged
        task.billing_status = "settled"
        await db.flush()
        return {"charged": charged, "refunded": 0}

    user = await _lock_user(db, int(task.requested_by)) if task.requested_by else None
    if not user:
        task.billing_charged_fen = charged
        if events:
            task.billing_status = "settled"
        await db.flush()
        return {"charged": charged, "refunded": 0}
    if task.billing_status != "frozen":
        # 非 frozen（none/settled 重入外）但有用量：按实扣直接扣钱包，避免只记账漏扣
        if charged > 0 and billing_active(user):
            await _ledger(
                db,
                user,
                -charged,
                "settle",
                ref_type="task_run",
                ref_id=str(task_id),
                note="settle_direct",
            )
        task.billing_charged_fen = charged
        if events or charged > 0:
            task.billing_status = "settled"
        await db.flush()
        if charged > 0:
            from app.services.billing.alerts import process_billing_alerts_after_charge

            if prepared_settlements is None:
                await process_billing_alerts_after_charge(db, user, charged_fen=charged)
            else:
                prepared_settlements.check_admin_after_commit = True
                await process_billing_alerts_after_charge(db, user, charged_fen=charged, defer_admin_check=True)
        return {"charged": charged, "refunded": 0}

    frozen_for_task = int(task.billing_estimate_fen or 0)
    user.frozen_fen = max(0, int(user.frozen_fen or 0) - frozen_for_task)
    extra = max(0, charged - frozen_for_task)
    refund = max(0, frozen_for_task - charged)

    if extra > 0:
        logger.warning(
            "settle overage user=%s task=%s extra_fen=%s balance=%s",
            user.id,
            task_id,
            extra,
            user.balance_fen,
        )
        await _ledger(
            db,
            user,
            -extra,
            "settle",
            ref_type="task_run",
            ref_id=str(task_id),
            note="settle_overage",
        )
    if refund > 0:
        await _ledger(
            db,
            user,
            refund,
            "unfreeze",
            ref_type="task_run",
            ref_id=str(task_id),
            note="refund_unused_freeze",
        )

    task.billing_charged_fen = charged
    task.billing_refunded_fen = refund
    task.billing_status = "settled"
    await _ledger(
        db,
        user,
        0,
        "settle",
        ref_type="task_run",
        ref_id=str(task_id),
        note=f"charged={charged} freeze={frozen_for_task} refund={refund}",
    )
    await db.flush()
    if charged > 0 and user:
        from app.services.billing.alerts import process_billing_alerts_after_charge

        if prepared_settlements is None:
            await process_billing_alerts_after_charge(db, user, charged_fen=charged)
        else:
            prepared_settlements.check_admin_after_commit = True
            await process_billing_alerts_after_charge(db, user, charged_fen=charged, defer_admin_check=True)
    return {"charged": charged, "refunded": refund}


# 终态与结算之间的正常窗口：_complete/_fail/_mark_cancelled 都是先置终态再 settle，
# 对账扫描须越过该窗口，避免与进行中的正常收尾竞争。
TERMINAL_FROZEN_RECONCILE_GRACE_SEC = 120

_RECONCILABLE_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


async def reconcile_terminal_frozen_tasks(db: AsyncSession, *, limit: int = 100) -> int:
    """对账补偿：终态但 billing_status 仍 frozen 的任务重新结算。

    来源是 settle_task 异常中断（如结算瞬间 DB 故障）的遗留行；不处理会造成
    冻结额长期不退、usage_events 悬空。settle_task 自带钱包流水幂等门闩，重复调用安全。
    注意：finalizing 窗口内的任务状态是 awaiting_poll/cancel_requested（非终态），
    不会被本扫描误伤，窗口协程按实结算的语义不受影响。
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=TERMINAL_FROZEN_RECONCILE_GRACE_SEC)
    stmt = (
        select(TaskRun)
        .where(
            TaskRun.billing_status == "frozen",
            TaskRun.status.in_(_RECONCILABLE_TERMINAL_STATUSES),
            TaskRun.finished_at.is_not(None),
            TaskRun.finished_at < cutoff,
        )
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    fixed = 0
    for task in rows:
        try:
            await settle_task(db, int(task.id))
            fixed += 1
        except Exception:  # noqa: BLE001
            logger.exception("reconcile terminal frozen task failed task_id=%s", task.id)
    if fixed:
        await db.commit()
        logger.warning("reconciled terminal frozen task runs count=%s", fixed)
    return fixed


async def credit_topup(
    db: AsyncSession,
    user: User,
    credit_fen: int,
    *,
    ref_type: str,
    ref_id: str,
    note: str = "",
) -> None:
    locked = await _lock_user(db, int(user.id))
    target = locked or user
    await _ledger(db, target, int(credit_fen), "topup", ref_type=ref_type, ref_id=ref_id, note=note)
    if locked is not None and locked is not user:
        user.balance_fen = locked.balance_fen
        user.frozen_fen = locked.frozen_fen
    await db.flush()


async def close_expired_pending_orders(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    out_trade_no: str | None = None,
) -> int:
    from datetime import datetime, timezone

    from app.models import Order

    now = datetime.now(timezone.utc)
    stmt = select(Order).where(Order.status == "pending")
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    if out_trade_no is not None:
        stmt = stmt.where(Order.out_trade_no == out_trade_no)
    rows = (await db.execute(stmt)).scalars().all()
    closed = 0
    for order in rows:
        created = order.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        # Stripe Checkout 会话 30 分钟有效，易支付 5 分钟
        window = 1800 if getattr(order, "provider", "epay") == "stripe" else 300
        if (now - created).total_seconds() < window:
            continue
        order.status = "closed"
        closed += 1
    if closed:
        await db.commit()
    return closed


async def settle_usage_charge(
    db: AsyncSession,
    user: User,
    charge_fen: int,
    *,
    ref_type: str = "api",
    ref_id: str = "",
) -> None:
    """即时扣费（已由 task 结算覆盖时仅作兼容）。"""
    if not billing_active(user):
        return
    need = max(0, int(charge_fen))
    if need <= 0:
        return
    locked = await _lock_user(db, int(user.id))
    target = locked or user
    available = int(target.balance_fen or 0)
    if available < need:
        raise InsufficientBalanceError(f"余额不足：需要 ¥{need / 100:.2f}，当前 ¥{available / 100:.2f}，请先充值")
    await _ledger(
        db,
        target,
        -need,
        "settle",
        ref_type=ref_type,
        ref_id=ref_id,
        note="api_usage",
    )
    if locked is not None and locked is not user:
        user.balance_fen = locked.balance_fen
        user.frozen_fen = locked.frozen_fen
    await db.flush()
    from app.services.billing.alerts import process_billing_alerts_after_charge

    await process_billing_alerts_after_charge(db, target, charged_fen=need)


async def get_task_usage_lines(db: AsyncSession, task_id: int) -> list[UsageEvent]:
    result = await db.execute(
        select(UsageEvent)
        .where(UsageEvent.task_run_id == task_id)
        .order_by(UsageEvent.id.asc())
    )
    return list(result.scalars().all())


async def get_task_billing_summary(db: AsyncSession, task_id: int) -> dict[str, Any]:
    task = await db.get(TaskRun, task_id)
    if not task:
        return {}
    lines = await get_task_usage_lines(db, task_id)
    return {
        "billing_status": task.billing_status,
        "billing_estimate_fen": int(task.billing_estimate_fen or 0),
        "billing_charged_fen": int(task.billing_charged_fen or 0),
        "billing_refunded_fen": int(task.billing_refunded_fen or 0),
        "usage_count": len(lines),
        "total_tokens": sum(int(l.total_tokens or 0) for l in lines),
        "lines": lines,
    }
