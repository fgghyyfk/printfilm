"""电商批量任务 HTTP 接口，仅处理鉴权、表单校验与业务调用。"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_ecom_jobs import EcomJobListOut, EcomJobOptionsOut, EcomJobOut, RefineParams
from app.services.ecom_jobs import service as svc
from app.services.public_errors import public_http_exception

class EcomJobRoute(APIRoute):
    """仅为本模块的路径、查询和表单校验提供中文错误。"""

    # 包装 FastAPI 自动参数校验，避免把默认英文详情返回给用户。
    def get_route_handler(self):
        original = super().get_route_handler()

        # 保留原路由鉴权与业务行为，只转换自动校验异常。
        async def localized_handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(400, "参数错误，请检查图片数量和选项") from exc

        return localized_handler


router = APIRouter(prefix="/tools/ecom-jobs", tags=["ecom-jobs"], route_class=EcomJobRoute)
logger = logging.getLogger(__name__)


# 把业务层异常映射成固定中文短句，未知异常仅记录服务端日志。
async def _guard(db: AsyncSession, operation):
    try:
        return await operation
    except (ValueError, LookupError) as exc:
        await db.rollback()
        raise public_http_exception(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("电商批量任务请求失败")
        raise HTTPException(502, "生成服务暂时不可用，请稍后再试") from exc


# 获取已实现功能的上限、参数和分档价格。
@router.get("/options", response_model=EcomJobOptionsOut)
async def options(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _guard(db, svc.get_options(db, user))


# 上传一批参考商品图，校验完成后原子建 job、items 与 TaskRun。
@router.post("/{kind}", response_model=EcomJobOut)
async def create(
    kind: str, files: list[UploadFile] = File(...), params: str = Form("{}"), title: str = Form(""),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    from app.services.ecom_jobs.kinds import validate

    try:
        raw = json.loads(params)
        clean = validate(kind, len(files), raw)
        if kind == "refine":
            clean = RefineParams.model_validate(clean).model_dump()
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(400, "参数错误，请检查图片数量和选项") from exc
    uploads = []
    for file in files:
        # 每张只读取上限加一字节，避免超大文件被一次载入内存。
        data = await file.read(svc.MAX_UPLOAD + 1)
        uploads.append((data, file.filename or ""))
    job = await _guard(db, svc.create_job(db, user, kind=kind, uploads=uploads, params=clean, title=title))
    return svc.to_out(job)


# 当前用户历史列表，筛选与分页全部在服务端执行。
@router.get("", response_model=EcomJobListOut)
async def list_jobs(
    page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=50), kind: str | None = None,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    jobs, total = await _guard(db, svc.list_jobs(db, user, page=page, page_size=page_size, kind=kind))
    return {"items": [svc.to_out(job) for job in jobs], "meta": {"total": total, "page": page, "page_size": page_size}}


# 用户隔离后的详情可用于前端轮询。
@router.get("/{job_id}", response_model=EcomJobOut)
async def detail(job_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return svc.to_out(await _guard(db, svc.get_job(db, user, job_id)))


# 单张失败重试，业务层在行锁内检查原任务是否结束。
@router.post("/{job_id}/items/{idx}/retry", response_model=EcomJobOut)
async def retry(job_id: int, idx: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return svc.to_out(await _guard(db, svc.retry_item(db, user, job_id, idx)))


# 下载本任务已完成图片组成的 ZIP。
@router.get("/{job_id}/download")
async def download(job_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    data, filename = await _guard(db, svc.build_zip(db, user, job_id))
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    })
