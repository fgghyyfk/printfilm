# -*- coding: utf-8 -*-
"""计费相关 HTTP 异常映射。"""
from __future__ import annotations

from fastapi import HTTPException

from app.services.public_errors import public_http_exception


def http_exception_for_value_error(exc: ValueError) -> HTTPException:
    """仅本站钱包异常返回402；上游与参数错误使用安全中文及稳定错误码。"""
    return public_http_exception(exc)
