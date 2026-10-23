"""手动调账流水列表：ref_type=admin 筛选与操作人邮箱。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.ledger import list_ledger
from app.models import WalletLedger
from tests.conftest import make_user


@pytest.mark.asyncio
async def test_admin_ledger_filters_ref_type_admin(db_session: AsyncSession) -> None:
    # 造一条后台调账 + 一条注册赠送，仅调账应命中 ref_type=admin
    user = await make_user(db_session, balance_fen=1000)
    user.email = f"ledger-user-{uuid.uuid4().hex[:8]}@example.com"
    admin = await make_user(db_session, balance_fen=0)
    admin.email = f"ledger-admin-{uuid.uuid4().hex[:8]}@example.com"
    admin.role = "admin"
    await db_session.flush()

    db_session.add_all(
        [
            WalletLedger(
                user_id=user.id,
                delta_fen=500,
                balance_after=1500,
                kind="grant",
                ref_type="admin",
                ref_id=str(admin.id),
                note="手动充值",
            ),
            WalletLedger(
                user_id=user.id,
                delta_fen=100,
                balance_after=100,
                kind="grant",
                ref_type="",
                ref_id="",
                note="signup_grant",
            ),
        ]
    )
    await db_session.commit()

    out = await list_ledger(
        user_id=None,
        kind=None,
        ref_type="admin",
        created_from=None,
        created_to=None,
        page=1,
        page_size=20,
        _admin=admin,
        db=db_session,
    )
    assert out.meta.total == 1
    assert len(out.items) == 1
    row = out.items[0]
    assert row.ref_type == "admin"
    assert row.note == "手动充值"
    assert row.user_email == user.email
    assert row.operator_email == admin.email
