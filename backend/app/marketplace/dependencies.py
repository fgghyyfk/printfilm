"""复用现有身份，只增加下游开关与文件服务依赖。"""
import asyncio
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin, get_current_user, security, _user_from_bearer
from app.models import User
from .config import get_marketplace_settings
from . import storage

DB = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(get_current_admin)]


def enabled() -> None:
    """关闭时不允许通过直接 URL 进入下游接口。"""
    if not get_marketplace_settings().enabled:
        raise HTTPException(404, "下游业务未启用")


async def storage_ready() -> None:
    """在异步请求外核验 OSS，禁止阻塞事件循环。"""
    await asyncio.to_thread(storage.bucket)


async def optional_user(db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> User | None:
    """公开详情允许访客，私密访问仍要求有效用户。"""
    return await _user_from_bearer(db, creds.credentials) if creds else None


Visitor = Annotated[User | None, Depends(optional_user)]
