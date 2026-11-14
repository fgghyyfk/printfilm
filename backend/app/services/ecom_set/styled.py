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
from app.services.ecom_set import categories, typo
from app.services.ecom_set.infoscreens import DEFAULT_COPY, SIZE_SLOT
from app.services.ecom_set.planner import plan_set
from app.services.ecom_set.presets import FREE_REFS, SLOTS, gen_ratio, slots_for
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


async def _pick_scene(db: AsyncSession, user: User, scene_key: str | None) -> dict | None:
    """取场景并做快照；没选场景返回 None。"""
    from app.services.ecom_set import scenes

    key = (scene_key or "").strip()
    if not key:
        return None
    return scenes.snapshot(await scenes.get_scene(db, user.id, key))


def _scene_note(scene: dict | None, *, with_props: bool = True) -> str:
    """给规划模型的场景约束：每张图只写机位、构图与道具变化，背景、台面、光线沿用同一场景。"""
    if not scene:
        return ""
    from app.services.ecom_set import festivals
    from app.services.ecom_set.scenes import scene_text

    note = (f"整套图统一使用「{scene['name']}」场景（{scene_text(scene.get('spec') or {}, with_props=with_props)}）。"
            "每张图的 scene 只写机位、构图和道具的变化，背景、台面、光线与色调保持这一场景")
    fest = festivals.of_scene(scene.get("key"))
    return f"「{fest.name}」节日主题。{note}" if fest else note


def _festival_note(scene: dict | None) -> str:
    """节日场景的文案语气约束（放进规划的补充说明，不受备注 500 字截断影响）。"""
    from app.services.ecom_set import festivals

    fest = festivals.of_scene((scene or {}).get("key"))
    return festivals.copy_note(fest) if fest else ""


async def _element_pool(db: AsyncSession, user: User, *, category: str, keys: list[str] | None,
                        knowledge: dict, scene: dict | None) -> list[dict]:
    """本套图的元素池：用户选了就用所选；日化没选时按商品资料推荐 + 场景道具自动组成，保证每张能错开。"""
    from app.services.ecom_set import elements, variety

    if keys:
        return elements.snapshot(await elements.get_many(db, user, keys))
    if category not in {"goods", "tool"}:
        return []
    industry = next(iter((scene or {}).get("industries") or []), "")
    # 节日场景：节日包 + 商品资料命中的元素；否则按商品资料推荐
    rec = elements.snapshot(await elements.recommend(db, user, knowledge=knowledge, industry=industry, limit=8,
                                                     scene_key=(scene or {}).get("key") or ""))
    from app.services.ecom_set import festivals

    if festivals.of_scene((scene or {}).get("key")):
        return rec  # 节日包已覆盖场景道具，再叠加会出现「红果枝」「红果枝（南天竹）」这类重复
    scene_props = variety.pool_from_scene(scene)
    names = {e["name"] for e in rec}
    return rec + [e for e in scene_props if e["name"] not in names]


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
    data: bytes | None, filename: str, fabric: tuple[bytes, str] | None, scene_key: str | None = None,
    image_engine: str | None = None, element_keys: list[str] | None = None,
    dims: dict | None = None, no_text: bool = False,
) -> int:
    """规划一套「模特上身」图，返回套图 id。商品库里的款名/尺寸/材质/卖点会作为默认值。

    dims：本次填写的实际尺寸（厘米），会记回商品库；留空用商品库里的尺寸。no_text：成图不排文字、不留文字区。
    """
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
        dims = await _remember_dims(db, product, dims)
    name = clip(name, 60)
    if not name:
        raise ValueError("请填写款名")
    size = categories.normalize_size(category, size)
    material = categories.normalize_material(category, material)
    frame = categories.normalize_frame(frame)
    from app.services.ecom_set import dims as dims_mod

    dims = {} if category in dims_mod.NO_DIMS_CATEGORIES else dims_mod.normalize(dims or {})
    slots = slots_for(category, count, slots_picked)
    know, variants = _knowledge_of(product)
    slots, skipped = _filter_info_slots(slots, know, variants, dims)
    model = await _pick_model(db, user, model_asset_id, required=any(s.ref == "model" for s in slots))
    scene = await _pick_scene(db, user, scene_key)
    engine = _pick_engine(image_engine)
    pool = await _element_pool(db, user, category=category, keys=element_keys, knowledge=know, scene=scene)
    color_note = f"本款颜色：{images['color_name']}" if images.get("color_name") else ""
    plan_text = categories.plan_notes(category, size=size, material=material,
                                      notes="；".join(x for x in (dims_mod.plan_note(dims), notes.strip(), color_note,
                                                                   _scene_note(scene, with_props=not pool)) if x))
    path = Path(images["image_path"])

    async def _exec() -> dict:
        from app.services.ecom_set.knowledge import digest

        from app.services.ecom_set.variety import hint_text

        result = await plan_set(image_path=path, name=name, notes=clip(plan_text, 500), platform="taobao",
                                style="minimal", slots=slots, knowledge=digest(know),
                                extra="\n".join(x for x in (hint_text(slots, pool), _festival_note(scene)) if x))
        await _record_plan_charge(db, user.id)
        return result

    billing_task, plan = await run_billed_ephemeral(
        db, user, domain="studio", task_type="ecom_set_plan", executor=_exec,
        payload={"count": len(slots), "platform": frame}, commit=False,
    )
    styled = {"category": category, "size": size, "material": material, "frame": frame,
              **(model or {}), **{k: v for k, v in images.items() if k not in {"image_path", "image_url"}}}
    if scene:
        styled["scene"] = scene
    styled["type_style"] = typo.default_type_style(category, scene)
    if dims:
        styled["dims"] = dims
    if no_text:
        styled["no_text"] = True
    styled["image_engine"] = engine
    if pool:
        from app.services.ecom_set.variety import assign

        styled["elements"] = pool
        styled["layout"] = assign(slots, plan["items"], pool)
    elif any(s.ref == "composite" for s in slots):
        from app.services.ecom_set.variety import assign

        styled["layout"] = assign(slots, plan["items"], [])
    if any(s.ref in {"info", "ingredients"} for s in slots):
        styled.update(knowledge=know, variants=variants, product_path=str(path), product_name=name,
                      material_label=categories.material_label(category, material))
    plan["warnings"] = [*skipped, *plan["warnings"]]
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
        copy = ({**it["copy"], **categories.cover_copy(category, name=name, size=size, material=material)}
                if slot.key == cover_key else it["copy"])
        if slot.key == SIZE_SLOT:
            # 尺寸标注用固定标题，规划模型写的营销句放在这里不合适（用户仍可改）
            title, subtitle, en = DEFAULT_COPY[SIZE_SLOT]
            copy = {"title": title, "subtitle": subtitle, "en": en, "bullets": []}
        # 不加文字：AI 槽位不再留文字区，画面铺满（免费的信息屏、尺寸图本身就是文字/标注，照常排）
        blank = "none" if no_text and slot.ref not in FREE_REFS | {"ingredients"} else slot.blank
        db.add(EcomSetItem(set_id=ecom.id, idx=idx, slot=slot.key, layout=slot.layout,
                           ratio=gen_ratio(frame, slot.key), blank=blank, scene=it["scene"],
                           status="planned", copy_json=copy))
    await db.commit()
    return ecom.id


async def _remember_dims(db: AsyncSession, product, dims: dict | None) -> dict:
    """本次填了尺寸就记回商品库（下次自动带出）；没填用商品库里的尺寸。"""
    from app.services.ecom_set import dims as dims_mod

    picked = dims_mod.normalize(dims or {})
    if not picked:
        return dims_mod.normalize(product.dims or {})
    if product.category not in dims_mod.NO_DIMS_CATEGORIES and picked != dims_mod.normalize(product.dims or {}):
        product.dims = picked
    return picked


def _pick_engine(value: str | None) -> str:
    """出图模型：选了 GPT Image 2 但后台没开通生图渠道时报错。"""
    from app.services.ecom_set.service import normalize_engine
    from app.services.openai_image import openai_image_configured

    engine = normalize_engine(value)
    if engine == "gpt-image-2" and not openai_image_configured():
        raise ValueError("GPT Image 2 未开通：请管理员在后台「系统设置 → 生图」启用并填写 Key，或改用 Seedream")
    return engine


def _knowledge_of(product) -> tuple[dict, list[dict]]:
    """商品资料快照 + 各颜色款（名称、图片路径），给规划与信息屏用。"""
    from app.services.ecom_set import knowledge as kn

    if product is None:
        return {}, []
    know = kn.normalize(product.knowledge or {}) if not kn.is_empty(product.knowledge) else {}
    variants = [{"name": c.get("name") or "", "image_path": c.get("image_path") or ""}
                for c in (product.colors or [])[:12]]
    return know, variants


def _filter_info_slots(slots: list, know: dict, variants: list[dict], dims: dict | None = None) -> tuple[list, list[str]]:
    """去掉资料不够的信息屏并给出提示；全部被去掉时报错。"""
    from app.services.ecom_set import infoscreens, ingredientscreen

    keep, skipped = [], []
    for s in slots:
        if s.ref == "ingredients" and not ingredientscreen.available(know):
            skipped.append(f"「{s.label}」需要商品资料里至少两个成分，本次已跳过；可在「我的商品」上传参考详情整理资料后再生成")
            continue
        if s.ref == "info" and not infoscreens.available(s.key, know, variants, dims):
            hint = ("可在确认页或「我的商品」填上实际尺寸后再生成" if s.key == infoscreens.SIZE_SLOT
                    else "可在「我的商品」上传参考详情整理资料后再生成")
            skipped.append(f"「{s.label}」需要{infoscreens.missing_hint(s.key)}，本次已跳过；{hint}")
            continue
        keep.append(s)
    if not keep:
        raise ValueError(skipped[0] if skipped else "请至少选择一张要生成的图")
    return keep, skipped


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
        "scene_key": (info.get("scene") or {}).get("key") or "", "scene_name": (info.get("scene") or {}).get("name") or "",
        "image_engine": info.get("image_engine") or "seedream",
        "dims": info.get("dims") or {}, "no_text": bool(info.get("no_text")),
        "elements": [{"key": e["key"], "name": e["name"], "image_url": e.get("image_url") or ""}
                     for e in info.get("elements") or []],
        "type_style": typo.normalize_type_style(info.get("type_style"),
                                                typo.default_type_style(cat, info.get("scene"))),
    }


def slot_is_free(slot_key: str) -> bool:
    """该槽位是否免费（不调模型）。"""
    slot = SLOTS.get(slot_key)
    return bool(slot and slot.ref in FREE_REFS)
