"""Shared Pydantic primitives used across schema modules."""

from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from app.services.public_errors import public_drama_params, public_message


# 仅响应字段使用序列化别名：不修改输入、ORM或原始历史记录。
PublicErrorText = Annotated[str, PlainSerializer(public_message)]
PublicDramaParams = Annotated[dict, PlainSerializer(public_drama_params)]


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
