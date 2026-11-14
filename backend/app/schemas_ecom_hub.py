"""电商工作台轻量历史与带货关联响应。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas_ecom import EcomToProjectIn


class RecentEcomTask(BaseModel):
    """任务摘要，类型和 ID 共同标识任务。"""
    id: int
    kind: Literal["set", "refine", "style", "buyer_show", "sku", "translate"]
    title: str
    status: str
    created_at: datetime


class EcomRecentMeta(BaseModel):
    """服务端分页信息。"""
    page: int
    page_size: int
    total: int


class EcomRecentOut(BaseModel):
    """聚合任务历史。"""
    items: list[RecentEcomTask]
    meta: EcomRecentMeta


class EcomVideoLink(BaseModel):
    """最近关联的带货项目，尚未创建时为空。"""
    project_id: int | None = None


class EcomJobToProjectIn(EcomToProjectIn):
    """批量出图缺少套图规划，创建视频时补充品名和卖点。"""
    name: str = Field(default="", max_length=128)
    notes: str = Field(default="", max_length=500)


class ShotMotionIn(BaseModel):
    """带货分镜切换动态/静图；action 取白名单 key，detail 为一句动作补充。"""
    motion: bool
    action: str | None = Field(default=None, max_length=32)
    detail: str | None = Field(default=None, max_length=60)
