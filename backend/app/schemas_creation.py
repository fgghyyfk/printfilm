"""Internal planner result for draining persisted legacy creation tasks."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
