"""各业务模块共用的响应结构。"""

from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer

from app.services.public_errors import public_drama_params, public_message


# 仅响应字段使用序列化别名：不修改输入、ORM或原始历史记录。
PublicErrorText = Annotated[str, PlainSerializer(public_message)]
PublicDramaParams = Annotated[dict, PlainSerializer(public_drama_params)]


class PageMeta(BaseModel):
    """服务端分页信息。"""

    page: int = Field(description="当前页码，从 1 开始。")
    page_size: int = Field(description="每页条数。")
    total: int = Field(description="符合筛选条件的记录总数。")
