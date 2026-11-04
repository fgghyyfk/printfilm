"""一图出套图 API：/api/tools/ecom-set/*（只做鉴权、校验与调用 service）。"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_ecom import (
    EcomCopyIn,
    EcomPlanUpdateIn,
    EcomRegenerateIn,
    EcomSetListOut,
    EcomSetOptionsOut,
    EcomSetOut,
    EcomToProjectIn,
    EcomToProjectOut,
)
from app.services.ecom_set import service as svc

router = APIRouter(prefix="/tools/ecom-set", tags=["ecom-set"])
logger = logging.getLogger(__name__)


# 把 service 异常转成中文短句的 HTTP 错误
async def _guard(db: AsyncSession, coro):
    try:
        return await coro
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("ecom-set 请求失败")
        raise HTTPException(status_code=502, detail="生成服务暂时不可用，请稍后再试") from exc


# 序列化套图
async def _out(db: AsyncSession, ecom) -> EcomSetOut:
    return EcomSetOut(**svc.to_out(ecom, image_fen=await svc.image_unit_fen(db)))


# 表单选项与价格
@router.get("/options", response_model=EcomSetOptionsOut)
async def options(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return EcomSetOptionsOut(**await svc.get_options(db))


# 上传商品图并规划（同步，收一次规划费）
@router.post("/plan", response_model=EcomSetOut)
async def create_plan(
    file: UploadFile | None = File(default=None, description="商品图；从「我的商品」选择时可不传"),
    name: str = Form(""),
    platform: str = Form("taobao"),
    style: str = Form("minimal"),
    count: int = Form(6),
    notes: str = Form(""),
    category: str = Form("general", description="general 通用商品；scarf 丝巾详情页"),
    scarf_size: str = Form("", description="丝巾尺寸：53 / 68 / 88 / 110（cm），仅 category=scarf"),
    model_asset_id: int | None = Form(default=None, ge=1, description="丝巾详情页使用的模特（模特库资产 id）；留空用默认模特"),
    scarf_material: str = Form("", description="材质 id（随品类不同，如丝巾 silk / silk_wool）"),
    frame: str = Form("", description="输出画幅：taobao_34 淘宝详情 3:4 / main_11 主图 1:1 / xhs_34 小红书 3:4"),
    slots: str = Form("", description="勾选的槽位 key，逗号分隔；留空按张数用默认构成"),
    product_id: int | None = Form(default=None, ge=1, description="「我的商品」id；传入时用其颜色款图片与资料"),
    color_index: int = Form(0, ge=0, le=20, description="颜色款序号（从 0 开始）"),
    fabric: UploadFile | None = File(default=None, description="面料/材质实拍图（可选，作为质感参考）"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await file.read() if file is not None else None
    fabric_upload = (await fabric.read(), fabric.filename or "fabric.jpg") if fabric is not None else None
    picked = [k.strip() for k in slots.split(",") if k.strip()] or None
    ecom = await _guard(db, svc.create_plan(
        db, user, data=data, filename=(file.filename if file else "") or "", name=name,
        platform=platform, style=style, count=count, notes=notes,
        category=category, scarf_size=scarf_size or None, model_asset_id=model_asset_id,
        scarf_material=scarf_material or None, frame=frame or None, slots_picked=picked,
        product_id=product_id, color_index=color_index, fabric=fabric_upload,
    ))
    return await _out(db, ecom)


# 我的套图（分页）
@router.get("", response_model=EcomSetListOut)
async def list_sets(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows, total = await svc.list_sets(db, user, page=page, page_size=page_size)
    unit = await svc.image_unit_fen(db)
    return EcomSetListOut(
        items=[EcomSetOut(**svc.to_out(r, image_fen=unit)) for r in rows],
        total=total, page=page, page_size=page_size,
    )


# 套图详情（前端轮询）
@router.get("/{set_id}", response_model=EcomSetOut)
async def get_set(set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _out(db, await _guard(db, svc.get_set(db, user, set_id)))


# 生成前修改方案
@router.put("/{set_id}/plan", response_model=EcomSetOut)
async def update_plan(
    set_id: int, body: EcomPlanUpdateIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    items = [it.model_dump() for it in body.items]
    return await _out(db, await _guard(db, svc.update_plan(db, user, set_id, items)))


# 确认方案，批量生成
@router.post("/{set_id}/generate", response_model=EcomSetOut)
async def generate(set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _out(db, await _guard(db, svc.start_generation(db, user, set_id)))


# 单张重新生成
@router.post("/{set_id}/items/{idx}/regenerate", response_model=EcomSetOut)
async def regenerate(
    set_id: int, idx: int, body: EcomRegenerateIn | None = None,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    scene = body.scene if body else None
    return await _out(db, await _guard(db, svc.regenerate_item(db, user, set_id, idx, scene)))


# 单张改文案（重新排版，免费）
@router.put("/{set_id}/items/{idx}/copy", response_model=EcomSetOut)
async def update_copy(
    set_id: int, idx: int, body: EcomCopyIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    return await _out(db, await _guard(db, svc.update_copy(db, user, set_id, idx, body.model_dump())))


# 打包下载 zip
@router.get("/{set_id}/download")
async def download(set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    data, filename = await _guard(db, svc.build_zip(db, user, set_id))
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# 详情长图（已完成的图按顺序竖向拼接）
@router.get("/{set_id}/long")
async def long_image(set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    data, filename = await _guard(db, svc.long_image(db, user, set_id))
    return Response(content=data, media_type="image/jpeg",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# 同款换色：沿用方案，换商品库里的另一个颜色款，新建一套待生成
@router.post("/{set_id}/recolor", response_model=EcomSetOut)
async def recolor(set_id: int, body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        index = int(body.get("color_index"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="请选择颜色款") from exc
    return await _out(db, await _guard(db, svc.recolor(db, user, set_id, index)))


# 用套图生成「商品带货」静图口播项目（已有则直接返回）
@router.post("/{set_id}/project", response_model=EcomToProjectOut)
async def to_project(
    set_id: int, body: EcomToProjectIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    from app.services.daihuo.service import create_project_from_set

    project = await _guard(db, create_project_from_set(
        db, user, set_id, output_ratio=body.output_ratio, voice_id=body.voice_id,
    ))
    return EcomToProjectOut(project_id=project.id)
