"""电商批量任务 API 参数和响应结构。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas_common import PageMeta


class RefineParams(BaseModel):
    """精修参数：背景、比例、清晰度和可选备注。"""

    background: Literal["white", "keep", "transparent"] = "white"
    ratio: Literal["1:1", "3:4", "原比例"] = "1:1"
    clarity: Literal["2k", "4k"] = "2k"
    notes: str = Field(default="", max_length=500)


class StyleParams(BaseModel):
    """风格复刻参数：单图合成或逐产品批量生成。"""

    model_config = {"extra": "forbid"}
    mode: Literal["single", "batch"] = "single"
    count: int = Field(default=1, ge=1, le=4, strict=True)
    ratio: Literal["1:1", "3:4", "9:16", "16:9"] = "1:1"
    clarity: Literal["2k", "4k"] = "2k"
    notes: str = Field(default="", max_length=500)


class EcomInputOut(BaseModel):
    """对外参考图仅公开角色和可访问 URL。"""

    role: str
    url: str


class EcomJobItemOut(BaseModel):
    """一张输出的状态与下载地址。"""

    idx: int
    inputs: list[EcomInputOut]
    prompt: str
    ratio: str
    size: str
    result_url: str | None = None
    status: str
    error: str | None = None
    task_run_id: int | None = None


class EcomJobOut(BaseModel):
    """批量任务与图片详情。"""

    id: int
    kind: str
    title: str
    status: str
    params: dict
    created_at: datetime
    updated_at: datetime
    items: list[EcomJobItemOut]


class EcomJobListOut(BaseModel):
    """服务端分页后的批量任务列表。"""

    items: list[EcomJobOut]
    meta: PageMeta


class EcomKindOut(BaseModel):
    """可用 kind 的上限、比例、清晰度和单张价格（分）。"""

    max_images: int
    mode_limits: dict[str, int] | None = None
    max_count: int = 1
    ratios: list[str]
    clarities: list[str]
    prices: dict[str, int]


class EcomJobOptionsOut(BaseModel):
    """当前可用电商功能与计费开关。"""

    kinds: dict[str, EcomKindOut]
    billing_enabled: bool
