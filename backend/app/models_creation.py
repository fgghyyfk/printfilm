"""Durable conversations and independent production versions for short videos."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CreationSession(Base):
    __tablename__ = "creation_sessions"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id", name="uq_creation_session_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="新的创作")
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"), default="anim_3d")
    status: Mapped[str] = mapped_column(String(32), default="drafting", index=True)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    # Logical pointers avoid circular foreign keys during bootstrap/deletion.
    latest_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_turn_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CreationMessage(Base):
    __tablename__ = "creation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "client_message_id", name="uq_creation_client_message"),
        UniqueConstraint("reply_to_message_id", name="uq_creation_reply"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("creation_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    client_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CreationRun(Base):
    __tablename__ = "creation_runs"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_creation_run_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("creation_sessions.id", ondelete="CASCADE"), index=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("creation_messages.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="generating", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="planning")
    copy_text: Mapped[str] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, unique=True)
    current_task_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
