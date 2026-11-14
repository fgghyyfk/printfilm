"""团队模式增量迁移：旧个人记录均保持 team_id=NULL，不搬迁余额或历史账单。"""
from sqlalchemy import text


async def apply_team_schema_patches(conn) -> None:
    """兼容既有 PostgreSQL 库，新增字段与索引均可重复执行。"""
    # 兼容早期本地团队原型表，保留旧余额字段与已有内容。
    await conn.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS description VARCHAR(180) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS color VARCHAR(7) NOT NULL DEFAULT '#435c4b'"))
    await conn.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"))
    await conn.execute(text("ALTER TABLE team_members ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT true"))
    await conn.execute(text("ALTER TABLE team_members ADD COLUMN IF NOT EXISTS monthly_limit_fen INTEGER"))
    await conn.execute(text("ALTER TABLE team_members ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT now()"))
    legacy = set((await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='teams'"))).scalars().all())
    if {"balance_fen", "frozen_fen"} <= legacy:
        await conn.execute(text("ALTER TABLE teams ALTER COLUMN balance_fen SET DEFAULT 0"))
        await conn.execute(text("ALTER TABLE teams ALTER COLUMN frozen_fen SET DEFAULT 0"))
        await conn.execute(text("INSERT INTO team_wallets(team_id,balance_fen,frozen_fen) SELECT id,balance_fen,frozen_fen FROM teams ON CONFLICT(team_id) DO NOTHING"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_team_membership ON team_members(team_id,user_id)"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_team_active_owner ON team_members(team_id) WHERE role='owner' AND active IS TRUE"))
    for table in ("drama_projects", "task_runs", "usage_events", "orders"):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id)"))
        await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_team_id ON {table}(team_id)"))
    await conn.execute(text("ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS billing_enabled_snapshot BOOLEAN"))
    await conn.execute(text("ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS billing_period VARCHAR(7)"))
    await conn.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS monthly_limit_fen INTEGER"))
    for table in ("task_runs", "usage_events"):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS billing_payer_user_id INTEGER REFERENCES users(id)"))
        await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_billing_payer_user_id ON {table}(billing_payer_user_id)"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_team_admin_adjust_request ON team_wallet_ledger(team_id,ref_id) WHERE ref_type='admin_adjust'"))
