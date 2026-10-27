"""Read-only catalog records; IDs remain namespaced by their original source."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr
from app.schemas_media_references import MediaReferenceOut

MediaKind = Literal["image", "video", "audio"]
LibraryType = Literal["all", "character", "scene", "prop", "voice", "image", "video", "audio"]
LibraryProjectKind = Literal["drama", "short_video"]
LibraryGenerationStatus = Literal["all", "generated", "ungenerated"]


class MediaLibraryItem(BaseModel):
    _lineage: tuple | None = PrivateAttr(default=None)
    _reference_task_id: int | None = PrivateAttr(default=None)
    key: str
    id: int
    source_kind: Literal["drama", "tool", "drama_fragment", "task", "short_video"]
    source_id: int
    type: str
    asset_type: str
    media_kind: MediaKind
    name: str | None = None
    cover: str | None = None
    url: str | None = None
    params: dict | None = None
    references: list[MediaReferenceOut] = Field(default_factory=list)
    project_id: int | None = None
    project_kind: LibraryProjectKind | None = None
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
