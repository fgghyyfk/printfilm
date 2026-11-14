# -*- coding: utf-8 -*-
"""计费相关 HTTP 异常映射。"""
from __future__ import annotations

from fastapi import HTTPException

from app.services.public_errors import InsufficientBalanceError, public_http_exception

# 本站钱包（含团队钱包、批量预估）自身抛出的余额不足前缀；上游文案不会走到这些固定前缀
_LOCAL_WALLET_PREFIXES = ("余额不足：", "团队余额不足", "团队所有者余额不足")


def http_exception_for_value_error(exc: ValueError) -> HTTPException:
    """仅本站钱包异常返回402；媒体参数校验返回422；异常自带 status_code（如限频 429）时沿用；
    其余上游与参数错误使用安全中文及稳定错误码。"""
    from app.services.media_models import MediaValidationError

    detail = str(exc)
    if isinstance(exc, MediaValidationError):
        return HTTPException(status_code=422, detail=[{"loc": ["body", exc.field], "msg": detail, "type": "media_parameter"}])
    if not isinstance(exc, InsufficientBalanceError) and type(exc) is ValueError and detail.startswith(_LOCAL_WALLET_PREFIXES):
        # 团队钱包 / 批量预估仍抛普通 ValueError，文案为本站固定模板，可原样返回
        return HTTPException(status_code=402, detail=detail, headers={"X-Error-Code": "insufficient_balance"})
    mapped = public_http_exception(exc)
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status != mapped.status_code:
        return HTTPException(status_code=status, detail=mapped.detail, headers=mapped.headers)
    return mapped
