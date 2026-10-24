"""独立创作工具请求 / 响应。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ToolRunOut(BaseModel):
    kind: str = Field(description="image | video | audio")
    run_id: int | None = None
    platform_task_id: int | None = None
    urls: list[str] = Field(default_factory=list)
    task_id: str | None = None
    status: str = "succeeded"
    preview_url: str | None = None
    message: str | None = None


class ToolTaskOut(BaseModel):
    status: str
    kind: str = "video"
    urls: list[str] = Field(default_factory=list)
    error: str | None = None
    platform_task_id: int | None = None
    provider_task_id: str | None = None
    queue_type: str | None = None
    preview_url: str | None = None


class ToolRunRecordOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tool_id: str
    kind: str
    status: str
    prompt: str = ""
    preview_url: str | None = None
    urls: list[str] = Field(default_factory=list)
    task_id: str | None = None
    params: dict | None = None
    error: str | None = None
    created_at: str
    platform_task_id: int | None = None
    provider_task_id: str | None = None
    provider_name: str | None = None
    provider_status: str | None = None
    queue_type: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    billing_charged_fen: int = 0


class ToolRunListOut(BaseModel):
    items: list[ToolRunRecordOut]
    total: int
    page: int
    page_size: int
    counts: dict[str, int] = Field(default_factory=dict)
