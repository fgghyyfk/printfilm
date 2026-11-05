"""电商图「模特上身」套图：建套图（商品库/上传、颜色款、模特、材质、尺寸、画幅、勾选槽位、面料参考）、
同款换色、详情长图。丝巾 / 服饰 / 首饰 / 包袋共用。
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models_ecom import EcomSet, EcomSetItem
from app.services.billing import run_billed_ephemeral
from app.services.ecom_set import categories
from app.services.ecom_set.planner import plan_set
from app.services.ecom_set.presets import SLOTS, gen_ratio, slots_for
from app.services.ecom_set.prompts import clip

LONG_WIDTH = 1080


async def _pick_model(db: AsyncSession, user: User, model_asset_id: int | None, required: bool) -> dict | None:
    """取模特（指定的，或模特库默认模特）；有模特槽位却没有模特时报错。"""
    from app.services import tool_models

    if model_asset_id:
        model = await tool_models.get_model(db, user.id, model_asset_id)
    else:
        model = next((m for m in await tool_models.list_models(db, user.id) if tool_models.is_default_model(m)), None)
    if model is None:
        if required:
            raise ValueError("请先选择模特：在模特库里加入或生成一位模特，并设为默认")
        return None
    return {"model_asset_id": model.id, "model_name": model.name or "", "model_url": model.url or model.cover or ""}


async def _resolve_images(db: AsyncSession, user: User, *, product_id: int | None, color_index: int,
                          data: bytes | None, filename: str, fabric: tuple[bytes, str] | None) -> dict:
    """确定商品图与面料图：优先商品库（颜色款），否则用本次上传。"""
    from app.services.ecom_set import products
    from app.services.ecom_set.jobs import public_url
    from app.services.ecom_set.service import save_product_image

    out: dict = {"product_id": None, "color_index": 0, "color_name": "", "fabric_path": "", "fabric_url": ""}
    if product_id:
        row = await products.get_product(db, user, product_id)
        color = products.color_of(row, color_index)
        out.update(product_id=row.id, color_index=color_index, color_name=color.get("name") or "",
                   image_path=color["image_path"], image_url=color["image_url"], product=row,
                   real_url=color.get("real_image_url") or "",
                   fabric_path=row.fabric_image_path or "", fabric_url=row.fabric_image_url or "")
    else:
        if not data:
            raise ValueError("请上传商品图，或从「我的商品」里选择")
        path = save_product_image(user.id, data, filename)
        out.update(image_path=str(path), image_url=await asyncio.to_thread(public_url, path))
    if fabric is not None:
        fpath = save_product_image(user.id, *fabric)
        out.update(fabric_path=str(fpath), fabric_url=await asyncio.to_thread(public_url, fpath))
    return out


async def create_styled_plan(
    db: AsyncSession, user: User, *, category: str, name: str, notes: str, count: int | str | None,
    size: str | None, material: str | None, model_asset_id: int | None, frame: str | None,
    slots_picked: list[str] | None, product_id: int | None, color_index: int,
    data: bytes | None, filename: str, fabric: tuple[bytes, str] | None,
) -> int:
    """规划一套「模特上身」图，返回套图 id。商品库里的款名/尺寸/材质/卖点会作为默认值。"""
    from app.services.ecom_set.service import _record_plan_charge

    images = await _resolve_images(db, user, product_id=product_id, color_index=color_index,
                                   data=data, filename=filename, fabric=fabric)
    product = images.pop("product", None)
    if product is not None:
        category = product.category
        name = name or product.name
        size = size or product.size
        material = material or product.material
        notes = notes or product.notes
    name = clip(name, 60)
    if not name:
        raise ValueError("请填写款名")
    size = categories.normalize_size(category, size)
    material = categories.normalize_material(category, material)
    frame = categories.normalize_frame(frame)
    slots = slots_for(category, count, slots_picked)
    model = await _pick_model(db, user, model_asset_id, required=any(s.ref == "model" for s in slots))
    color_note = f"本款颜色：{images['color_name']}" if images.get("color_name") else ""
    plan_text = categories.plan_notes(category, size=size, material=material,
                                      notes="；".join(x for x in (notes.strip(), color_note) if x))
    path = Path(images["image_path"])

    async def _exec() -> dict:
        result = await plan_set(image_path=path, name=name, notes=clip(plan_text, 500), platform="taobao",
                                style="minimal", slots=slots)
        await _record_plan_charge(db, user.id)
        return result

    billing_task, plan = await run_billed_ephemeral(
        db, user, domain="studio", task_type="ecom_set_plan", executor=_exec,
        payload={"count": len(slots), "platform": frame}, commit=False,
    )
    styled = {"category": category, "size": size, "material": material, "frame": frame,
              **(model or {}), **{k: v for k, v in images.items() if k not in {"image_path", "image_url"}}}
    ecom = EcomSet(
        user_id=user.id, status="planned", name=name, platform=frame, style="minimal", count=len(slots),
        notes=clip(plan_text, 500), product_image_path=str(path), product_image_url=images["image_url"],
        plan={"product": plan["product"], "warnings": plan["warnings"], "vision": plan["vision"], "styled": styled},
        plan_task_id=billing_task.id,
    )
    db.add(ecom)
    await db.flush()
    cover_key = f"{category}_cover"
    for idx, (slot, it) in enumerate(zip(slots, plan["items"], strict=True)):
        copy = (categories.cover_copy(category, name=name, size=size, material=material)
                if slot.key == cover_key else it["copy"])
        db.add(EcomSetItem(set_id=ecom.id, idx=idx, slot=slot.key, layout=slot.layout,
                           ratio=gen_ratio(frame, slot.key), blank=slot.blank, scene=it["scene"],
                           status="planned", copy_json=copy))
    await db.commit()
    return ecom.id


async def recolor_set(db: AsyncSession, user: User, set_id: int, color_index: int) -> int:
    """同款换色：沿用原套图的画面与文案，换成商品库里的另一个颜色款，新建一套（免规划费），返回新套图 id。"""
    from app.services.ecom_set import products
    from app.services.ecom_set.service import get_set

    src = await get_set(db, user, set_id)
    styled = dict((src.plan or {}).get("styled") or {})
    if not styled.get("product_id"):
        raise ValueError("这套图不是从「我的商品」创建的，无法一键换色")
    row = await products.get_product(db, user, int(styled["product_id"]))
    color = products.color_of(row, color_index)
    styled.update(color_index=color_index, color_name=color.get("name") or "", real_url=color.get("real_image_url") or "")
    plan = {**(src.plan or {}), "styled": styled}
    ecom = EcomSet(
        user_id=user.id, status="planned", name=src.name, platform=src.platform, style=src.style, count=src.count,
        notes=src.notes, product_image_path=color["image_path"], product_image_url=color["image_url"], plan=plan,
    )
    db.add(ecom)
    await db.flush()
    for it in src.items:
        db.add(EcomSetItem(set_id=ecom.id, idx=it.idx, slot=it.slot, layout=it.layout, ratio=it.ratio,
                           blank=it.blank, scene=it.scene, copy_json=dict(it.copy_json or {}), status="planned"))
    await db.commit()
    return ecom.id


def build_long_image(paths: list[Path], width: int = LONG_WIDTH) -> bytes:
    """把成图按顺序竖向拼成一张详情长图（宽 1080），返回 JPEG 字节。"""
    ims = []
    for p in paths:
        with Image.open(p) as src:
            im = ImageOps.exif_transpose(src).convert("RGB")
        ims.append(im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS))
    if not ims:
        raise ValueError("还没有生成好的图片")
    canvas = Image.new("RGB", (width, sum(i.height for i in ims)), (255, 255, 255))
    y = 0
    for im in ims:
        canvas.paste(im, (0, y))
        y += im.height
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def styled_out(ecom: EcomSet) -> dict | None:
    """序列化「模特上身」信息（旧丝巾套图存在 plan.scarf 下）。"""
    plan = ecom.plan or {}
    info = plan.get("styled") or plan.get("scarf")
    if not isinstance(info, dict):
        return None
    cat = info.get("category") or "scarf"
    size = info.get("size") or ""
    size_label = ""
    if cat == "scarf":
        from app.services.ecom_set import scarf as scarf_mod

        size_label = scarf_mod.size_label(size)
    return {
        "category": cat, "size": size, "size_label": size_label or size,
        "material": categories.normalize_material(cat, info.get("material")),
        "material_label": categories.material_label(cat, info.get("material")),
        "frame": categories.normalize_frame(info.get("frame") or ecom.platform),
        "model_asset_id": info.get("model_asset_id"), "model_name": info.get("model_name") or "",
        "model_url": info.get("model_url") or "", "product_id": info.get("product_id"),
        "color_index": info.get("color_index") or 0, "color_name": info.get("color_name") or "",
        "fabric_url": info.get("fabric_url") or "", "real_url": info.get("real_url") or "",
    }


def slot_is_free(slot_key: str) -> bool:
    """该槽位是否免费（不调模型）。"""
    slot = SLOTS.get(slot_key)
    return bool(slot and slot.ref == "flat")
