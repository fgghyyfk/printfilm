"""一图出套图接口的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    error: str | None = None
    billable: bool = True


class EcomScarfOut(BaseModel):
    """「模特上身」套图信息：品类、尺寸、材质、画幅、模特、商品库颜色款与面料图。"""

    category: str = "scarf"
    size: str = ""
    size_label: str = ""
    material: str = "silk"
    material_label: str = ""
    frame: str = "taobao_34"
    model_asset_id: int | None = None
    model_name: str = ""
    model_url: str = ""
    product_id: int | None = None
    color_index: int = 0
    color_name: str = ""
    fabric_url: str = ""
    real_url: str = ""
    scene_key: str = ""
    scene_name: str = ""


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
    product_image_url: str
    product: EcomProductOut
    warnings: list[str]
    image_fen: int
    total_fen: int
    items: list[EcomSetItemOut]
    created_at: str
    project_id: int | None = None
    category: str = "general"
    scarf: EcomScarfOut | None = None
    styled: EcomScarfOut | None = None


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


class EcomSetOptionsOut(BaseModel):
    """表单选项与价格。"""

    platforms: list[EcomOptionOut]
    styles: list[EcomOptionOut]
    counts: list[int]
    categories: list[EcomOptionOut] = []
    scarf_sizes: list[EcomOptionOut] = []
    scarf_counts: list[int] = []
    scarf_materials: list[EcomOptionOut] = []
    styled: list[dict] = []
    frames: list[EcomOptionOut] = []
    industries: list[EcomOptionOut] = []
    category_industry: dict[str, str] = {}
    plan_fen: int
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

    # 上限须覆盖各品类的全部可选槽位（丝巾 11 个，官方预设最多 10 张）
    items: list[EcomPlanItemIn] = Field(max_length=12)


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


class EcomToProjectOut(BaseModel):
    """带货项目 id（前端随后调 /projects/{id}/generate 写稿）。"""

    project_id: int
