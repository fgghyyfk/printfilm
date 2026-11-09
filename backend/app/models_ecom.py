"""一图出套图 ORM：一次套图（ecom_sets）与其中每张图（ecom_set_items）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    # 商品带货：由本套图生成的科普项目（projects.id）
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


class EcomProduct(Base):
    """「我的商品」：一款商品录一次（多个颜色款、材质、尺寸、卖点），以后出图直接选。"""

    __tablename__ = "ecom_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(16), default="scarf")
    size: Mapped[str] = mapped_column(String(32), default="")
    material: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # 颜色款：[{"name": "米白", "image_path": "...", "image_url": "..."}]
    colors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 面料/材质实拍（可选），作为质感参考
    fabric_image_path: Mapped[str] = mapped_column(String(1024), default="")
    fabric_image_url: Mapped[str] = mapped_column(String(1024), default="")
    # 参考详情图：[{"path", "url", "kind": "own" 品牌自己的 | "competitor" 竞品}]
    ref_images: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 商品资料（读参考详情整理 + 用户修改）：结构见 services/ecom_set/knowledge.py
    knowledge: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EcomScene(Base):
    """电商图场景库：系统场景（行业预设，启动时只补缺失）与用户自己的场景。"""

    __tablename__ = "ecom_scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # system 系统预设 | user 我的场景（品牌场景包后续扩展）
    scope: Mapped[str] = mapped_column(String(16), default="system", index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    industries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    # 场景卡字段：backdrop / palette / surface / props / light / mood / bg_color / product_ratio / blank / prompt
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EcomElement(Base):
    """电商图元素库：系统预置元素（启动只补缺失）与用户自定义元素（文字或实物照片）。"""

    __tablename__ = "ecom_elements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # system 系统预置 | user 我的元素
    scope: Mapped[str] = mapped_column(String(16), default="system", index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    # plant / produce / natural / vessel / fabric / water / light / festival
    category: Mapped[str] = mapped_column(String(16), default="plant", index=True)
    # 给 AI 的画面描述
    prompt: Mapped[str] = mapped_column(String(200), default="")
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    industries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # s 小 / m 中 / l 大（氛围类）
    scale: Mapped[str] = mapped_column(String(4), default="s")
    # 用户上传的实物照（生成时作为额外参考图）
    image_path: Mapped[str] = mapped_column(String(1024), default="")
    image_url: Mapped[str] = mapped_column(String(1024), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
