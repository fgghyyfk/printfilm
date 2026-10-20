"""管理端用户调账：按增减金额（balance_delta_fen）而非设为目标余额。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.users import patch_user
from app.models import WalletLedger
from app.schemas import AdminUserPatch
from tests.conftest import make_user


@pytest.mark.asyncio
async def test_admin_patch_user_balance_delta_increase(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, balance_fen=0)
    admin.email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin.role = "admin"
    user = await make_user(db_session, balance_fen=1000)
    user.email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    await db_session.commit()

    out = await patch_user(
        user_id=user.id,
        body=AdminUserPatch(balance_delta_fen=500, balance_note="补款"),
        admin=admin,
        db=db_session,
    )
    assert out.balance_fen == 1500

    ledgers = (
        await db_session.execute(
            select(WalletLedger).where(
                WalletLedger.user_id == user.id,
                WalletLedger.ref_type == "admin",
            )
        )
    ).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].delta_fen == 500
    assert ledgers[0].kind == "grant"
    assert ledgers[0].note == "补款"
    assert ledgers[0].ref_id == str(admin.id)


@pytest.mark.asyncio
async def test_admin_patch_user_balance_delta_decrease(db_session: AsyncSession) -> None:
    admin = await make_user(db_session, balance_fen=0)
    admin.email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin.role = "admin"
    user = await make_user(db_session, balance_fen=1000)
    user.email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    await db_session.commit()

    out = await patch_user(
        user_id=user.id,
        body=AdminUserPatch(balance_delta_fen=-300),
        admin=admin,
        db=db_session,
    )
    assert out.balance_fen == 700

    row = (
        await db_session.execute(
            select(WalletLedger).where(WalletLedger.user_id == user.id, WalletLedger.ref_type == "admin")
        )
    ).scalar_one()
    assert row.delta_fen == -300
    assert row.kind == "adjust"


@pytest.mark.asyncio
async def test_admin_patch_user_balance_delta_rejects_negative_result(
    db_session: AsyncSession,
) -> None:
    from fastapi import HTTPException

    admin = await make_user(db_session, balance_fen=0)
    admin.email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin.role = "admin"
    user = await make_user(db_session, balance_fen=100)
    user.email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await patch_user(
            user_id=user.id,
            body=AdminUserPatch(balance_delta_fen=-200),
            admin=admin,
            db=db_session,
        )
    assert exc.value.status_code == 400
    assert "负" in str(exc.value.detail)
