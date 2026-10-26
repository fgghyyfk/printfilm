"""Read-only catalog records; IDs remain namespaced by their original source."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MediaKind = Literal["image", "video", "audio"]
LibraryType = Literal["all", "character", "scene", "prop", "voice", "image", "video", "audio"]


class MediaLibraryItem(BaseModel):
    key: str
    id: int
    source_kind: Literal["drama", "tool"]
    source_id: int
    type: str
    asset_type: str
    media_kind: MediaKind
    name: str | None = None
    cover: str | None = None
    url: str | None = None
    params: dict | None = None
    project_id: int | None = None
    project_title: str | None = None
    project_url: str | None = None
    updated_at: datetime | None = None


class MediaLibraryMeta(BaseModel):
    total: int
    total_all: int
    page: int
    page_size: int


class MediaLibraryPage(BaseModel):
    items: list[MediaLibraryItem]
    meta: MediaLibraryMeta
