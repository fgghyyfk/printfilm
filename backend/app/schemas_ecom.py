"""一图出套图接口的请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas_common import PublicErrorText


class EcomSetItemOut(BaseModel):
    """套图中的一张。"""

    idx: int
    slot: str
    slot_label: str
    layout: str
    ratio: str
    scene: str
    title: str
    subtitle: str
    bullets: list[str]
    base_url: str | None = None
    final_url: str | None = None
    status: str
    error: PublicErrorText | None = None


class EcomProductOut(BaseModel):
    """规划识别出的商品信息。"""

    name: str = ""
    category: str = ""
    colors: list[str] = Field(default_factory=list)
    selling_points: list[str] = Field(default_factory=list)


class EcomSetOut(BaseModel):
    """一套图的完整状态。"""

    id: int
    status: str
    name: str
    platform: str
    style: str
    count: int
    language: Literal["zh", "en", "ja", "none"] = "zh"
    clarity: Literal["2k", "4k"] = "2k"
    product_image_url: str
    product: EcomProductOut
    warnings: list[str]
    image_fen: int
    total_fen: int
    items: list[EcomSetItemOut]
    created_at: str
    project_id: int | None = None
    source_set_id: int | None = None


class EcomSetListOut(BaseModel):
    """套图分页列表。"""

    items: list[EcomSetOut]
    total: int
    page: int
    page_size: int


class EcomOptionOut(BaseModel):
    """下拉/chips 选项。"""

    id: str
    name: str


class EcomSlotOut(EcomOptionOut):
    """槽位说明、默认版式与是否主图。"""

    description: str
    ratio: str
    layout: str
    is_main: bool


class EcomSetOptionsOut(BaseModel):
    """表单选项与价格。"""

    platforms: list[EcomOptionOut]
    styles: list[EcomOptionOut]
    counts: list[int]
    slots: list[EcomSlotOut]
    languages: list[str]
    clarities: list[str]
    prices: dict[str, int]
    plan_fen: int
    translate_fen: int
    image_fen: int
    billing_enabled: bool


class EcomPlanItemIn(BaseModel):
    """修改方案中的一张。"""

    idx: int = Field(ge=0)
    scene: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=60)
    bullets: list[str] = Field(default_factory=list, max_length=3)


class EcomPlanUpdateIn(BaseModel):
    """修改方案请求。"""

    items: list[EcomPlanItemIn] = Field(max_length=10)


class EcomRegenerateIn(BaseModel):
    """单张重做请求（可改画面描述）。"""

    scene: str | None = Field(default=None, max_length=300)


class EcomCopyIn(BaseModel):
    """单张改文案请求。"""

    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=60)
    bullets: list[str] = Field(default_factory=list, max_length=3)


class EcomToProjectIn(BaseModel):
    """用套图生成带货视频项目。"""

    output_ratio: str = Field(default="9:16", pattern=r"^(9:16|16:9|1:1|4:3)$")
    voice_id: str = Field(default="", max_length=128)
    create_new: bool = False
    request_id: str | None = Field(default=None, min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class EcomToProjectOut(BaseModel):
    """带货项目 id（前端随后调 /projects/{id}/generate 写稿）。"""

    project_id: int


class EcomTranslateIn(BaseModel):
    """从已完成套图创建指定语言的独立排版副本。"""

    model_config = {"extra": "forbid"}
    language: Literal["en", "ja", "zh"]
