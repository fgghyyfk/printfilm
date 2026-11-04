"""将异常格式化为可展示、非空的错误文案。"""

from __future__ import annotations

from app.services.public_errors import public_error


def format_exception_message(
    exc: BaseException,
    *,
    fallback: str = "未知错误",
    limit: int = 500,
) -> str:
    """公开失败文案只使用固定模板；原异常由调用方记录日志。"""
    return public_error(exc, fallback=fallback).message[:limit]
