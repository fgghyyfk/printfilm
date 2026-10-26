"""团队人工编辑租约；数据库行和递增代次共同阻止失锁页面覆盖内容。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeamEditLock(Base):
    """资源占用行长期复用，释放后保留 fencing version。"""

    __tablename__ = "team_edit_locks"
    __table_args__ = (UniqueConstraint("project_id", "resource_type", "resource_id", name="uq_team_edit_resource"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("drama_projects.id", ondelete="CASCADE"), index=True)
    resource_type: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
