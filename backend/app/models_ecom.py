"""一图出套图 ORM：一次套图（ecom_sets）与其中每张图（ecom_set_items）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EcomSet(Base):
    """一次「一图出套图」：商品图 + 规划结果 + 整体状态。"""

    __tablename__ = "ecom_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # planned | generating | done | partial | failed
    status: Mapped[str] = mapped_column(String(16), default="planned", index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    platform: Mapped[str] = mapped_column(String(16), default="taobao")
    style: Mapped[str] = mapped_column(String(16), default="minimal")
    count: Mapped[int] = mapped_column(Integer, default=6)
    notes: Mapped[str] = mapped_column(Text, default="")
    product_image_path: Mapped[str] = mapped_column(String(1024), default="")
    product_image_url: Mapped[str] = mapped_column(String(1024), default="")
    # 规划得到的商品信息与提示：{"product": {...}, "warnings": [...], "vision": bool}
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 商品带货：最近创建的视频项目（projects.id），历史项目各自保留。
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list[EcomSetItem]] = relationship(
        back_populates="ecom_set",
        cascade="all, delete-orphan",
        order_by="EcomSetItem.idx",
        lazy="selectin",
    )


class EcomSetItem(Base):
    """套图中的一张：槽位、文案、底图/成图与生成状态（每张独立一行，避免并发写同一 JSON）。"""

    __tablename__ = "ecom_set_items"
    __table_args__ = (UniqueConstraint("set_id", "idx", name="uq_ecom_set_items_set_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("ecom_sets.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    slot: Mapped[str] = mapped_column(String(32), default="")
    layout: Mapped[str] = mapped_column(String(32), default="none")
    ratio: Mapped[str] = mapped_column(String(8), default="1:1")
    blank: Mapped[str] = mapped_column(String(16), default="top")
    scene: Mapped[str] = mapped_column(Text, default="")
    # {"title": str, "subtitle": str, "bullets": [str]}
    copy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    base_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    final_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # planned | queued | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), default="planned", index=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    task_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ecom_set: Mapped[EcomSet] = relationship(back_populates="items")


class EcomJob(Base):
    """一次电商批量出图任务；不同 kind 共用任务和计费底座。"""

    __tablename__ = "ecom_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    items: Mapped[list[EcomJobItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="EcomJobItem.idx", lazy="selectin"
    )


class EcomJobItem(Base):
    """批量任务的一张图，输入按产品、风格、模特或场景角色存储。"""

    __tablename__ = "ecom_job_items"
    __table_args__ = (UniqueConstraint("job_id", "idx", name="uq_ecom_job_items_job_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("ecom_jobs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    # 每个输入含 role、path、url；API 不暴露服务器本地路径。
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    prompt: Mapped[str] = mapped_column(Text, default="")
    ratio: Mapped[str] = mapped_column(String(24), default="1:1")
    size: Mapped[str] = mapped_column(String(24), default="2048x2048")
    result_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    task_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    job: Mapped[EcomJob] = relationship(back_populates="items")
