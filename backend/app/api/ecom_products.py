"""「我的商品」API：/api/tools/ecom-products/*（只做鉴权、校验与调用 service）。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services.ecom_set import dims as dims_mod
from app.services.ecom_set import products as svc

router = APIRouter(prefix="/tools/ecom-products", tags=["ecom-products"])
logger = logging.getLogger(__name__)


async def _guard(db: AsyncSession, coro):
    """service 异常转中文 HTTP 错误。"""
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
        logger.exception("ecom-products 请求失败")
        raise HTTPException(status_code=502, detail="服务暂时不可用，请稍后再试") from exc


def _names(raw: str, n: int) -> list[str]:
    """颜色名：JSON 数组或逗号分隔，按图片数补齐。"""
    try:
        parsed = json.loads(raw) if raw.strip().startswith("[") else raw.split(",")
    except ValueError:
        parsed = raw.split(",")
    names = [str(x).strip() for x in parsed][:n]
    return names + [""] * (n - len(names))


@router.get("", summary="我的商品列表")
async def list_products(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
                        db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    rows, total = await svc.list_products(db, user, page=page, page_size=page_size)
    return {"items": [svc.to_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.post("", summary="新建商品（多个颜色款平铺图 + 可选面料实拍）")
async def create_product(
    name: str = Form(...), category: str = Form("scarf"), size: str = Form(""), material: str = Form(""),
    notes: str = Form(""), color_names: str = Form("", description="颜色名，JSON 数组或逗号分隔，与 files 一一对应"),
    files: list[UploadFile] = File(..., description="各颜色款的商品图"),
    fabric: UploadFile | None = File(default=None, description="面料/材质实拍（可选）"),
    real_files: list[UploadFile] | None = File(default=None, description="实物图（可选），与 real_for 一一对应"),
    real_for: str = Form("", description="实物图对应的颜色款序号，JSON 数组或逗号分隔"),
    dim_h: str = Form("", max_length=8, description="实际高度（厘米，可空）"),
    dim_w: str = Form("", max_length=8, description="实际宽度（厘米，可空）"),
    dim_d: str = Form("", max_length=8, description="实际厚度/深度（厘米，可空）"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    names = _names(color_names, len(files))
    colors = [(names[i], await f.read(), f.filename or "image.jpg") for i, f in enumerate(files)]
    fab = (await fabric.read(), fabric.filename or "fabric.jpg") if fabric is not None else None
    reals: dict[int, tuple[bytes, str]] = {}
    targets = _names(real_for, len(real_files or []))
    for i, f in enumerate(real_files or []):
        try:
            reals[int(targets[i])] = (await f.read(), f.filename or "real.jpg")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="实物图对应的颜色款无效") from exc
    row = await _guard(db, svc.create_product(db, user, name=name, category=category, size=size, material=material,
                                              notes=notes, colors=colors, fabric=fab, reals=reals,
                                              dims=dims_mod.parse_form(dim_h, dim_w, dim_d)))
    return svc.to_out(row)


@router.get("/{product_id}", summary="商品详情")
async def get_product(product_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.get_product(db, user, product_id)))


@router.put("/{product_id}", summary="修改商品资料（款名、品类、尺寸、材质、卖点、颜色名）")
async def update_product(product_id: int, body: dict, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.update_product(db, user, product_id, body)))


@router.post("/{product_id}/colors", summary="追加颜色款")
async def add_color(product_id: int, name: str = Form(""), file: UploadFile = File(...),
                    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    row = await _guard(db, svc.add_color(db, user, product_id, name=name, data=await file.read(),
                                         filename=file.filename or "image.jpg"))
    return svc.to_out(row)


@router.delete("/{product_id}/colors/{index}", summary="删除颜色款")
async def remove_color(product_id: int, index: int, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.remove_color(db, user, product_id, index)))


@router.put("/{product_id}/colors/{index}/real", summary="设置颜色款的实物图")
async def set_color_real(product_id: int, index: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)) -> dict:
    row = await _guard(db, svc.set_color_real(db, user, product_id, index, (await file.read(), file.filename or "real.jpg")))
    return svc.to_out(row)


@router.delete("/{product_id}/colors/{index}/real", summary="清除颜色款的实物图")
async def clear_color_real(product_id: int, index: int, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.set_color_real(db, user, product_id, index, None)))


@router.put("/{product_id}/fabric", summary="设置面料实拍图")
async def set_fabric(product_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict:
    row = await _guard(db, svc.set_fabric(db, user, product_id, (await file.read(), file.filename or "fabric.jpg")))
    return svc.to_out(row)


@router.delete("/{product_id}/fabric", summary="清除面料实拍图")
async def clear_fabric(product_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.set_fabric(db, user, product_id, None)))


@router.post("/{product_id}/references", summary="上传参考详情图（品牌自己的或竞品）")
async def add_references(product_id: int, kind: str = Form("own", description="own 自己的 | competitor 竞品"),
                         files: list[UploadFile] = File(...), db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)) -> dict:
    items = [(await f.read(), f.filename or "ref.jpg") for f in files]
    return svc.to_out(await _guard(db, svc.add_references(db, user, product_id, kind=kind, files=items)))


@router.delete("/{product_id}/references/{index}", summary="删除一张参考详情图")
async def remove_reference(product_id: int, index: int, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.remove_reference(db, user, product_id, index)))


@router.post("/{product_id}/knowledge/extract", summary="AI 读参考详情整理商品资料（按读图批次计费）")
async def extract_knowledge(product_id: int, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.extract_knowledge(db, user, product_id)))


@router.put("/{product_id}/knowledge", summary="保存修改后的商品资料")
async def set_knowledge(product_id: int, body: dict, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)) -> dict:
    return svc.to_out(await _guard(db, svc.set_knowledge(db, user, product_id, body)))


@router.delete("/{product_id}", summary="删除商品资料（已生成的套图保留）")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    await _guard(db, svc.delete_product(db, user, product_id))
    return {"ok": True}
