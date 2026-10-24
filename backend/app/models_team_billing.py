"""月度成员用量与仅供历史兼容的团队钱包流水。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class TeamWallet(Base):
    __tablename__ = "team_wallets"
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    balance_fen: Mapped[int] = mapped_column(Integer, default=0)
    frozen_fen: Mapped[int] = mapped_column(Integer, default=0)


class TeamWalletLedger(Base):
    __tablename__ = "team_wallet_ledger"
    __table_args__ = (
        Index("uq_team_admin_adjust_request", "team_id", "ref_id", unique=True,
              postgresql_where=text("ref_type = 'admin_adjust'"),
              sqlite_where=text("ref_type = 'admin_adjust'")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta_fen: Mapped[int] = mapped_column(Integer)
    balance_after_fen: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    ref_type: Mapped[str] = mapped_column(String(32), default="")
    ref_id: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMonthlyUsage(Base):
    __tablename__ = "team_monthly_usage"
    __table_args__ = (UniqueConstraint("team_id", "user_id", "period", name="uq_team_monthly_member"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    spent_fen: Mapped[int] = mapped_column(Integer, default=0)
    reserved_fen: Mapped[int] = mapped_column(Integer, default=0)
