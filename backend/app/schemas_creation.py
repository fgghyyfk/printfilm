"""Public conversation snapshots; media remains represented by ProjectOut."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas import ProjectOut


class CreationSessionCreate(BaseModel):
    template_id: str | None = None
    message: str | None = Field(default=None, max_length=12000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class CreationMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    client_message_id: str = Field(min_length=1, max_length=128)
    template_id: str | None = None

    @field_validator("content", "client_message_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入内容")
        return value


class CreationMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}


class CreationRunOut(BaseModel):
    id: int
    version: int
    status: str
    phase: str
    copy_text: str
    error_message: str | None
    project_id: int | None
    project: ProjectOut | None = None
    model_config = {"from_attributes": True}


class CreationSessionOut(BaseModel):
    id: int
    title: str
    status: str
    template_id: str
    brief: dict = Field(default_factory=dict)
    messages: list[CreationMessageOut] = Field(default_factory=list)
    runs: list[CreationRunOut] = Field(default_factory=list)
    active_run_id: int | None = None


class CreationSessionListItem(BaseModel):
    id: int
    title: str
    status: str
    template_id: str
    updated_at: datetime
    model_config = {"from_attributes": True}


class CreationSessionListOut(BaseModel):
    items: list[CreationSessionListItem]


class CreationDecision(BaseModel):
    action: Literal["ask", "produce"]
    reply: str = Field(default="", max_length=4000)
    title: str = Field(default="", max_length=200)
    template_id: str | None = None
    copy_text: str = Field(default="", max_length=12000)
    brief: dict = Field(default_factory=dict)

    @field_validator("copy_text")
    @classmethod
    def strip_copy(cls, value: str) -> str:
        return value.strip()
