"""一图出套图 API：/api/tools/ecom-set/*（只做鉴权、校验与调用 service）。"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ecom_jobs import EcomJobRoute
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services.public_errors import public_http_exception
from app.schemas_ecom import (
    EcomCopyIn,
    EcomTranslateIn,
    EcomTypeStyleIn,
    EcomPlanUpdateIn,
    EcomRegenerateIn,
    EcomSetListOut,
    EcomSetOptionsOut,
    EcomSetOut,
    EcomToProjectIn,
    EcomToProjectOut,
)
from app.services.ecom_set import service as svc
from app.services.ecom_set.dims import parse_form as parse_dims

router = APIRouter(prefix="/tools/ecom-set", tags=["ecom-set"])
logger = logging.getLogger(__name__)


# 把 service 异常转成中文短句的 HTTP 错误
async def _guard(db: AsyncSession, coro):
    try:
        return await coro
    except LookupError as exc:
        await db.rollback()
        raise public_http_exception(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise public_http_exception(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("ecom-set 请求失败")
        raise HTTPException(status_code=502, detail="生成服务暂时不可用，请稍后再试") from exc


# 解析槽位表单：以 [ 开头按 JSON 数组，否则按逗号拆分；留空 → 按张数用默认构成。
# 列表含义由 service 按品类区分：通用商品 = 自定义模块（严格校验），模特上身/标品品类 = 勾选槽位。
def _parse_slots(raw: str | None) -> list[str] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        if text[0] in "[{\"":
            raise HTTPException(status_code=400, detail="槽位格式错误") from None
        return [k.strip() for k in text.split(",") if k.strip()] or None
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="槽位格式错误")
    return value


# 序列化套图
async def _out(db: AsyncSession, ecom) -> EcomSetOut:
    return EcomSetOut(**svc.to_out(ecom, image_fen=await svc.image_unit_fen(db, svc.engine_of(ecom), ecom.clarity or "2k")))


# 表单选项与价格
@router.get("/options", response_model=EcomSetOptionsOut)
async def options(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return EcomSetOptionsOut(**await svc.get_options(db))


# 上传商品图并规划（同步，收一次规划费）
@router.post("/plan", response_model=EcomSetOut)
async def create_plan(
    file: UploadFile | None = File(default=None, description="商品图；从「我的商品」或精修结果选择时可不传"),
    name: str = Form(""),
    platform: str = Form("taobao"),
    style: str = Form("minimal"),
    count: int = Form(6),
    notes: str = Form(""),
    language: str = Form("zh"),
    clarity: str = Form("2k"),
    source_job_id: int | None = Form(None),
    source_item_idx: int | None = Form(None),
    category: str = Form("general", description="general 通用商品；scarf 丝巾详情页"),
    scarf_size: str = Form("", description="丝巾尺寸：53 / 68 / 88 / 110（cm），仅 category=scarf"),
    model_asset_id: int | None = Form(default=None, ge=1, description="丝巾详情页使用的模特（模特库资产 id）；留空用默认模特"),
    scarf_material: str = Form("", description="材质 id（随品类不同，如丝巾 silk / silk_wool）"),
    frame: str = Form("", description="输出画幅：taobao_34 淘宝详情 3:4 / main_11 主图 1:1 / xhs_34 小红书 3:4"),
    slots: str | None = Form(None, description="槽位：JSON 数组为通用商品自定义槽位；逗号分隔为品类勾选槽位；留空按张数用默认构成"),
    product_id: int | None = Form(default=None, ge=1, description="「我的商品」id；传入时用其颜色款图片与资料"),
    color_index: int = Form(0, ge=0, le=20, description="颜色款序号（从 0 开始）"),
    fabric: UploadFile | None = File(default=None, description="面料/材质实拍图（可选，作为质感参考）"),
    scene_key: str = Form("", max_length=64, description="场景库里的场景 key；留空用品类默认场景"),
    image_engine: str = Form("seedream", max_length=32, description="出图模型：seedream | gpt-image-2"),
    element_keys: str = Form("", max_length=1200, description="元素库 key，逗号分隔（最多 12 个）；留空自动推荐"),
    dim_h: str = Form("", max_length=8, description="商品实际高度（厘米）；留空用商品库里的尺寸"),
    dim_w: str = Form("", max_length=8, description="商品实际宽度（厘米）"),
    dim_d: str = Form("", max_length=8, description="商品实际厚度/深度（厘米）"),
    no_text: bool = Form(False, description="成图不加文字（纯画面，之后可在套图页切回加文字）"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    slot_keys = _parse_slots(slots)
    # 品类槽位只接受字符串 key；通用商品的自定义模块交给 service 严格校验
    picked = ([k for k in slot_keys if isinstance(k, str)] or None) if slot_keys else None
    data = await file.read() if file is not None else None
    fabric_upload = (await fabric.read(), fabric.filename or "fabric.jpg") if fabric is not None else None
    ecom = await _guard(db, svc.create_plan(
        db, user, data=data, filename=(file.filename if file else "") or "", name=name,
        platform=platform, style=style, count=count, notes=notes, slot_keys=slot_keys, language=language,
        clarity=clarity, source_job_id=source_job_id, source_item_idx=source_item_idx,
        category=category, scarf_size=scarf_size or None, model_asset_id=model_asset_id,
        scarf_material=scarf_material or None, frame=frame or None, slots_picked=picked,
        product_id=product_id, color_index=color_index, fabric=fabric_upload, scene_key=scene_key or None,
        image_engine=image_engine,
        element_keys=[k.strip() for k in element_keys.split(",") if k.strip()][:12] or None,
        dims=parse_dims(dim_h, dim_w, dim_d) or None, no_text=no_text,
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
    units: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (svc.engine_of(r), r.clarity or "2k")
        if key not in units:
            units[key] = await svc.image_unit_fen(db, *key)
    return EcomSetListOut(
        items=[EcomSetOut(**svc.to_out(r, image_fen=units[(svc.engine_of(r), r.clarity or "2k")])) for r in rows],
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
    return await _out(db, await _guard(db, svc.regenerate_item(
        db, user, set_id, idx, scene, shot=body.shot if body else None, elements=body.elements if body else None)))


# 往本套元素池追加元素（单张换元素前用）
@router.post("/{set_id}/elements", response_model=EcomSetOut)
async def add_set_elements(set_id: int, body: dict, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    keys = [str(k) for k in (body.get("keys") or []) if k][:12]
    return await _out(db, await _guard(db, svc.add_set_elements(db, user, set_id, keys)))


# 单张改文案（重新排版，免费）
@router.put("/{set_id}/items/{idx}/copy", response_model=EcomSetOut)
async def update_copy(
    set_id: int, idx: int, body: EcomCopyIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    return await _out(db, await _guard(db, svc.update_copy(db, user, set_id, idx, body.model_dump())))


# 切换整套字体风格（重新排字，免费）
@router.put("/{set_id}/type-style", response_model=EcomSetOut)
async def set_type_style(
    set_id: int, body: EcomTypeStyleIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    return await _out(db, await _guard(db, svc.set_type_style(db, user, set_id, body.type_style)))


# 调整顺序（影响结果页、详情长图与 zip，免费）
@router.put("/{set_id}/order", response_model=EcomSetOut)
async def reorder(set_id: int, body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.ecom_set.ordering import reorder_items

    raw = body.get("order")
    if not isinstance(raw, list) or not raw or not all(isinstance(i, int) for i in raw):
        raise HTTPException(status_code=400, detail="请提供新的图片顺序")
    return await _out(db, await _guard(db, reorder_items(db, user, set_id, raw)))


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
        create_new=body.create_new, request_id=body.request_id, video_form=body.video_form,
    ))
    return EcomToProjectOut(project_id=project.id)


# 预估套图转带货视频的阶段费用
@router.post("/{set_id}/project/quote")
async def quote_project(
    set_id: int, body: EcomToProjectIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    from app.services.daihuo.service import quote_project_from_set

    return await _guard(db, quote_project_from_set(db, user, set_id, output_ratio=body.output_ratio,
                                                   voice_id=body.voice_id, video_form=body.video_form))


translate_router = APIRouter(route_class=EcomJobRoute)


# 已完成套图翻译文案并复用底图重排，仅收文本翻译费用。
@translate_router.post("/{set_id}/translate", response_model=EcomSetOut)
async def translate(
    set_id: int, body: EcomTranslateIn,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
):
    from app.services.ecom_set.translate import translate_set

    return await _out(db, await _guard(db, translate_set(db, user, set_id, body.language)))


router.include_router(translate_router)
