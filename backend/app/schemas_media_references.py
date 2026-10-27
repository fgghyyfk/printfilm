"""Public reference media captured for one generation request."""

from typing import Literal

from pydantic import BaseModel, Field


MediaReferenceKind = Literal["image", "video", "audio"]
MediaReferenceRole = Literal[
    "reference_image", "reference_video", "reference_audio", "first_frame", "last_frame",
]


class MediaReferenceOut(BaseModel):
    kind: MediaReferenceKind
    url: str = Field(max_length=8192)
    name: str | None = Field(default=None, max_length=200)
    asset_id: int | None = Field(default=None, gt=0)
    cover: str | None = Field(default=None, max_length=8192)
    role: MediaReferenceRole | None = None
