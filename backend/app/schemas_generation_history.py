"""Public, provider-independent generation history records."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from app.schemas_media_references import MediaReferenceOut


class GenerationRecordOut(BaseModel):
    id: int
    source: Literal["task", "tool"]
    record_key: str
    tool_id: str
    kind: Literal["video", "image", "audio", "text"]
    status: str
    domain: str | None = None
    task_type: str | None = None
    prompt: str = ""
    preview_url: str | None = None
    urls: list[str] = Field(default_factory=list)
    text_content: str = ""
    task_id: str | None = None
    params: dict = Field(default_factory=dict)
    references: list[MediaReferenceOut] = Field(default_factory=list)
    error: str | None = None
    created_at: str = ""
    platform_task_id: int | None = None
    provider_task_id: str | None = None
    provider_name: str | None = None
    provider_status: str | None = None
    queue_type: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    billing_status: str = "none"
    billing_charged_fen: int | None = 0


class GenerationListOut(BaseModel):
    items: list[GenerationRecordOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    counts: dict[str, int] = Field(default_factory=dict)
