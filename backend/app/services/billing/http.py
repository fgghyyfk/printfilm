# -*- coding: utf-8 -*-
"""计费相关 HTTP 异常映射。"""
from __future__ import annotations

from fastapi import HTTPException


def http_exception_for_value_error(exc: ValueError) -> HTTPException:
    """余额不足 → 402；异常自带 status_code（如限频 429）时用它；其余 ValueError → 400。"""
    detail = str(exc)
    status = getattr(exc, "status_code", None) or (402 if detail.startswith("余额不足") else 400)
    return HTTPException(status_code=status, detail=detail)
