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
    en: str = ""
    shot: str = ""
    shot_name: str = ""
    elements: list[dict] = []
    base_url: str | None = None
    final_url: str | None = None
    status: str
    error: PublicErrorText | None = None
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
    type_style: str = "bold"
    image_engine: str = "seedream"
    elements: list[dict] = []
    dims: dict = {}
    no_text: bool = False


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
    # 通用商品自定义模块、多语言与清晰度（get_options 总会返回；默认值只为兼容旧调用）
    slots: list[EcomSlotOut] = []
    languages: list[str] = []
    clarities: list[str] = []
    prices: dict[str, int] = {}
    categories: list[EcomOptionOut] = []
    scarf_sizes: list[EcomOptionOut] = []
    scarf_counts: list[int] = []
    scarf_materials: list[EcomOptionOut] = []
    styled: list[dict] = []
    frames: list[EcomOptionOut] = []
    industries: list[EcomOptionOut] = []
    category_industry: dict[str, str] = {}
    type_styles: list[dict] = []
    image_engines: list[dict] = []
    shots: list[dict] = []
    plan_fen: int
    translate_fen: int = 0
    image_fen: int
    billing_enabled: bool


class EcomPlanItemIn(BaseModel):
    """修改方案中的一张。"""

    idx: int = Field(ge=0)
    scene: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=60)
    bullets: list[str] = Field(default_factory=list, max_length=3)
    # 英文小标；不传（None）表示保持原值
    en: str | None = Field(default=None, max_length=60)
    shot: str | None = Field(default=None, max_length=32)
    elements: list[str] | None = Field(default=None, max_length=6)


class EcomPlanUpdateIn(BaseModel):
    """修改方案请求。"""

    # 上限须覆盖各品类的全部可选槽位（丝巾 11 个，日化 14 个）
    items: list[EcomPlanItemIn] = Field(max_length=16)


class EcomRegenerateIn(BaseModel):
    """单张重做请求（可改画面描述、镜头与元素）。"""

    scene: str | None = Field(default=None, max_length=300)
    shot: str | None = Field(default=None, max_length=32)
    elements: list[str] | None = Field(default=None, max_length=6)


class EcomCopyIn(BaseModel):
    """单张改文案请求。"""

    title: str = Field(default="", max_length=40)
    subtitle: str = Field(default="", max_length=60)
    bullets: list[str] = Field(default_factory=list, max_length=3)
    en: str | None = Field(default=None, max_length=60)


class EcomTypeStyleIn(BaseModel):
    """切换整套图的字体风格（只重新排字，免费）。"""

    type_style: str = Field(max_length=16)


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
