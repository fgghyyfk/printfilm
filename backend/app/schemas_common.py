"""各业务模块共用的响应结构。"""

from pydantic import BaseModel, Field


class PageMeta(BaseModel):
    """服务端分页信息。"""

    page: int = Field(description="当前页码，从 1 开始。")
    page_size: int = Field(description="每页条数。")
    total: int = Field(description="符合筛选条件的记录总数。")
