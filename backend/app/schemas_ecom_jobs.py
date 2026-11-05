"""电商批量任务 API 参数和响应结构。"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

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


class BuyerShowParams(BaseModel):
    """买家秀或种草图参数，每张输出独立排队。"""

    model_config = {"extra": "forbid"}
    mode: Literal["buyer", "seeding"] = "buyer"
    count: int = Field(default=1, ge=1, le=8, strict=True)
    ratio: Literal["1:1", "3:4", "9:16", "16:9"] = "3:4"
    clarity: Literal["2k", "4k"] = "2k"
    notes: str = Field(default="", max_length=500)


class SkuParams(BaseModel):
    """SKU 参数：空比例由场景推导，名称按产品顺序一一对应。"""

    model_config = {"extra": "forbid"}
    ratio: Literal["1:1", "3:4", "9:16", "16:9"] | None = None
    clarity: Literal["2k", "4k"] = "2k"
    notes: str = Field(default="", max_length=500)
    sku_names: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)


class EcomVariantsParams(BaseModel):
    """从一张已完成结果派生的输出数量。"""

    model_config = {"extra": "forbid"}
    count: int = Field(default=1, ge=1, le=8, strict=True)


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
