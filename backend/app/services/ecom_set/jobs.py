"""一图出套图：单张图的任务执行（底图生成 → 文字排版 → 回写），由任务平台调度。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_ecom import EcomSet, EcomSetItem
from app.models_tasks import TaskRun
from app.services import storage
from app.services.ecom_set.layout import mock_base, render
from app.services.ecom_set.presets import FREE_REFS, GEN_SIZE, SLOTS, output_size
from app.services.ecom_set.prompts import NEGATIVE_PROMPT, build_scene_prompt

logger = logging.getLogger(__name__)

ACTIVE_ITEM = {"queued", "running"}


# 某套图的媒体目录（挂在 p0 下，不占科普项目 id）
def set_dir(user_id: int, set_id: int) -> Path:
    path = storage.project_dir(0) / "ecom" / f"u{user_id}" / f"s{set_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# 本地文件转对外 URL（开 OSS 时同步上传）
def public_url(path: Path) -> str:
    local = storage.publish_local(path, sync=True)
    return storage.republish_url(local, sync=True) or local


# 按各张状态重算整套状态（锁住套图行，避免并发完成时互相覆盖）
async def refresh_set_status(db: AsyncSession, set_id: int) -> str:
    row = (
        await db.execute(select(EcomSet).where(EcomSet.id == set_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        return ""
    statuses = (
        await db.execute(select(EcomSetItem.status).where(EcomSetItem.set_id == set_id))
    ).scalars().all()
    if not statuses or all(s == "planned" for s in statuses):
        row.status = "planned"
    elif any(s in ACTIVE_ITEM for s in statuses):
        row.status = "generating"
    elif all(s == "succeeded" for s in statuses if s != "planned"):
        row.status = "done"
    elif any(s == "succeeded" for s in statuses):
        row.status = "partial"
    else:
        row.status = "failed"
    await db.flush()
    return row.status


# 兜底：任务已失败/取消但图片仍显示排队或生成中时，改成失败，避免前端永远轮询
async def reconcile_stuck_items(db: AsyncSession, ecom: EcomSet) -> bool:
    active = {it.task_run_id: it for it in ecom.items if it.status in ACTIVE_ITEM and it.task_run_id}
    if not active:
        return False
    rows = (
        await db.execute(
            select(TaskRun.id, TaskRun.status, TaskRun.error_message).where(TaskRun.id.in_(list(active)))
        )
    ).all()
    changed = False
    for task_id, status, message in rows:
        if status in {"failed", "cancelled", "canceled"}:
            it = active[task_id]
            it.status = "failed"
            it.error = (message or "生成失败")[:500]
            changed = True
    if changed:
        await refresh_set_status(db, ecom.id)
        await db.commit()
    return changed


# 本张的镜头与元素（建套图时分配，单张可改）
def item_layout(info: dict, item: EcomSetItem) -> tuple[Any, list[dict]]:
    from app.services.ecom_set import shots as shots_mod
    from app.services.ecom_set.variety import elements_of

    entry = ((info.get("layout") or {}).get(str(item.idx))) or {}
    return shots_mod.get(entry.get("shot")), elements_of(list(info.get("elements") or []), list(entry.get("elements") or []))


def element_refs(elements: list[dict]) -> list[str]:
    """带实物照的元素 → 额外参考图地址（最多 3 张）。"""
    return [e["image_url"] for e in elements if e.get("image_url")][:3]


# 出图模型对应的生图参数：GPT Image 2 走后台「生图」渠道（OpenAI 官方或 Promptoll）
def engine_kwargs(ecom: EcomSet, item: EcomSetItem) -> dict:
    from app.services.ecom_set.service import item_engine

    if item_engine(ecom, item) == "gpt-image-2":
        return {"image_provider": "openai", "model": "gpt-image-2", "ratio_hint": item.ratio}
    return {}


def usage_model(ecom: EcomSet, item: EcomSetItem) -> str:
    """写用量行用的模型名。"""
    from app.services.ecom_set.service import item_engine

    return "gpt-image-2" if item_engine(ecom, item) == "gpt-image-2" else get_settings().model_image


# 生成无字底图并落到本地，返回本地路径
async def generate_base(db: AsyncSession, ecom: EcomSet, item: EcomSetItem) -> Path:
    settings = get_settings()
    dest = set_dir(ecom.user_id, ecom.id) / f"base_{item.idx:02d}_{uuid.uuid4().hex[:6]}.jpg"
    product = Path(ecom.product_image_path)
    if settings.ark_mock:
        return await asyncio.to_thread(mock_base, product, dest, ratio=item.ratio, seed=f"{ecom.id}-{item.idx}")

    from app.services.ark import get_ark
    from app.services.drama.billing_util import record_seedream_image_usage

    slot = SLOTS[item.slot]
    scarf = scarf_info(ecom)
    if scarf is not None:
        from app.services.ecom_set import categories

        category = scarf.get("category") or "scarf"
        if categories.is_composite(category) and slot.ref in {"composite", "texture"}:
            return await generate_goods_base(db, ecom, item, scarf, dest)
        if slot.ref == "ingredients":
            from app.services.ecom_set.ingredientscreen import generate_grid

            return await generate_grid(db, ecom, item, scarf, dest)
        fabric = scarf.get("fabric_url") or ""
        real = scarf.get("real_url") or ""
        from app.services.ecom_set.elements import prompt_line

        _, els = item_layout(scarf, item)
        prompt = categories.build_prompt(category, slot_prompt=slot.prompt, scene=item.scene,
                                         ref=slot.ref, size=scarf.get("size"), material=scarf.get("material"),
                                         has_fabric=bool(fabric), has_real=bool(real),
                                         scene_card=categories.scene_of(scarf, with_props=not scarf.get("elements")),
                                         blank=item.blank, elements=prompt_line(els), dims=scarf.get("dims"))
        refs = [scarf["model_url"], ecom.product_image_url] if slot.ref == "model" and scarf.get("model_url") \
            else [ecom.product_image_url]
        if slot.ref in {"product", "hand"}:
            refs += element_refs(els)
        if real:
            refs.append(real)
        if fabric:
            refs.append(fabric)
    else:
        prompt = build_scene_prompt(slot=slot, scene=item.scene, style=ecom.style, blank=item.blank)
        refs = [ecom.product_image_url]
    result = await get_ark().gen_image(
        prompt,
        NEGATIVE_PROMPT,
        refs,
        project_id=0,
        shot_no=ecom.user_id,
        size=GEN_SIZE.get(item.ratio, GEN_SIZE["1:1"]),
        **engine_kwargs(ecom, item),
    )
    await record_seedream_image_usage(
        db,
        user_id=ecom.user_id,
        model=usage_model(ecom, item),
        domain="studio",
        image_result=result,
        extra_raw={"tool_id": "ecomset", "set_id": ecom.id, "idx": item.idx},
    )
    url = result.local_url or result.remote_url or ""
    if not url:
        raise RuntimeError("生图未返回图片")
    path = await storage.ensure_local_media(url, dest)
    if scarf is not None and (scarf.get("category") or "scarf") not in {"goods", "tool"}:
        path = await asyncio.to_thread(color_fix, path, product)
    return path


# 原图贴不回去时重画的品类与次数（日化沿用「保留 AI 所画」，工具铭牌不能由 AI 代画）
RETRY_CATEGORIES = frozenset({"tool"})
RETRY_ON_MISMATCH = 2


# 标品：合成槽位（摆位 → AI 搭景 → 原图贴回）或质地槽位（只拍内容物）
async def generate_goods_base(db: AsyncSession, ecom: EcomSet, item: EcomSetItem, info: dict, dest: Path) -> Path:
    from app.services.ark import get_ark
    from app.services.drama.billing_util import record_seedream_image_usage
    from app.services.ecom_set import categories, composite
    from app.services.ecom_set.scenes import bg_rgb, normalize_spec

    settings = get_settings()
    slot = SLOTS[item.slot]
    from app.services.ecom_set import shots as shots_mod
    from app.services.ecom_set.elements import prompt_line

    spec = normalize_spec(((info.get("scene") or {}).get("spec")) or {})
    shot, els = item_layout(info, item)
    category = info.get("category") if categories.is_composite(info.get("category")) else "goods"
    prompt = categories.build_prompt(category, slot_prompt=slot.prompt, scene=item.scene, ref=slot.ref, size=None,
                                     material=info.get("material"), has_fabric=False,
                                     scene_card=categories.scene_of(info, with_props=not info.get("elements")),
                                     blank=item.blank, shot=f"{shot.prompt}（{shot.name}）" if shot else "",
                                     elements=prompt_line(els), dims=info.get("dims"))
    w, h = composite.COMPOSITE_SIZE.get(item.ratio, composite.COMPOSITE_SIZE["3:4"])
    paths = await _goods_cutout_paths(db, ecom, info, lineup=item.slot == "goods_lineup")
    use_composite = slot.ref == "composite" and bool(paths)
    refs: list[str] = []
    stage_boxes: list = []
    cutouts: list = []
    if use_composite:
        cutouts = [shots_mod.transform(composite.load_cutout(p), shot if len(paths) == 1 else None) for p in paths]
        height = spec["product_ratio"] * (0.85 if len(cutouts) > 1 else 1.0)
        base = 0.86 if item.blank != "bottom" else 0.68
        aspect = float(sum(c.width / c.height for c in cutouts) / len(cutouts))
        if len(cutouts) == 1 and shot is not None:
            # 平放时按竖放的高度换算（躺倒后宽变高），保持商品真实大小
            ref_h = height * (cutouts[0].height / max(1, cutouts[0].width)) if shot.lay else height
            placements = [shots_mod.placement(shot, height=ref_h, blank=item.blank, frame_ratio=w / h, aspect=aspect)]
        else:
            placements = composite.lineup_placements(len(cutouts), height=height, base=base, aspect=aspect,
                                                     frame_ratio=w / h)
        stage, stage_boxes = await asyncio.to_thread(composite.place_products, cutouts, placements, (w, h), bg_rgb(spec))
        stage_path = dest.with_name(dest.stem + "_stage.jpg")
        await asyncio.to_thread(stage.save, stage_path, "JPEG", quality=94)
        refs = [await asyncio.to_thread(public_url, stage_path), *element_refs(els)]
    elif slot.ref == "composite":
        # 没有透明抠图：退回「商品参考图」直接生图（商品可能被重绘，前端已提示上传透明底 PNG）
        refs = [ecom.product_image_url, *element_refs(els)]
    elif els:
        refs = element_refs(els)
    # 电动工具铭牌、logo 不能由 AI 代画：原图贴不回去（AI 镜像 / 换角度）时重画，最多再试 RETRY_ON_MISMATCH 次
    attempts = 1 + (RETRY_ON_MISMATCH if use_composite and category in RETRY_CATEGORIES else 0)
    # 用户只为交付的那张付费：重画产生的额外调用由平台承担，次数记在用量行 retries 里
    result, path, out = None, dest, None
    for attempt in range(attempts):
        result = await get_ark().gen_image(prompt, NEGATIVE_PROMPT, refs or None, project_id=0, shot_no=ecom.user_id,
                                           size=f"{w}x{h}", **engine_kwargs(ecom, item))
        url = result.local_url or result.remote_url or ""
        if not url:
            break
        path = await storage.ensure_local_media(url, dest)
        if not use_composite:
            out = path
            break
        out = await asyncio.to_thread(_restore_composite, path, dest, cutouts, stage_boxes, (w, h),
                                      f"set={ecom.id} idx={item.idx} attempt={attempt}")
        if out is not None:
            break
    if result is not None:
        await record_seedream_image_usage(
            db, user_id=ecom.user_id, model=usage_model(ecom, item), domain="studio", image_result=result,
            extra_raw={"tool_id": "ecomset", "set_id": ecom.id, "idx": item.idx,
                       "mode": "composite" if use_composite else slot.ref, "retries": attempt},
        )
    if result is None or not (result.local_url or result.remote_url):
        raise RuntimeError("生图未返回图片")
    return out or path


def _restore_composite(path: Path, dest: Path, cutouts: list, stage_boxes: list, size: tuple[int, int],
                       tag: str) -> Path | None:
    """把原图商品贴回 AI 底图；定位不可信或全部角度不符时返回 None（调用方决定重画或保留 AI 原图）。"""
    from PIL import Image

    from app.services.ecom_set import composite

    w, h = size
    with Image.open(path) as src:
        ai = src.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    found = composite.locate(ai, cutouts, stage_boxes)
    if not composite.plausible(found, stage_boxes, (w, h)):
        # 定位不可信：不强行贴回造成错位
        logger.warning("标品贴回定位不可信 %s found=%s", tag, found)
        return None
    # 逐支检查贴合度：AI 把商品画歪 / 换了角度时不贴回（避免出现斜影重影）
    keep = [i for i, (c, f) in enumerate(zip(cutouts, found, strict=True))
            if composite.match_score(ai, c, f) >= composite.MATCH_MIN]
    if len(keep) < len(found):
        logger.warning("标品贴回：%s/%s 支与 AI 角度不符 %s", len(found) - len(keep), len(found), tag)
    if not keep:
        return None
    out = dest.with_name(dest.stem + "_cmp.jpg")
    composite.restore(ai, [cutouts[i] for i in keep], [found[i] for i in keep]).save(out, "JPEG", quality=94)
    return out


# 标品合成要用的透明抠图：多款陈列取商品库里所有带透明底的款（最多 6 款），否则只用当前款
async def _goods_cutout_paths(db: AsyncSession, ecom: EcomSet, info: dict, *, lineup: bool) -> list[Path]:
    from app.models_ecom import EcomProduct
    from app.services.ecom_set.composite import has_alpha

    main = Path(ecom.product_image_path)
    paths: list[Path] = []
    if lineup and info.get("product_id"):
        row = await db.get(EcomProduct, int(info["product_id"]))
        for color in (row.colors if row else None) or []:
            p = Path(str(color.get("image_path") or ""))
            if p.exists() and await asyncio.to_thread(has_alpha, p):
                paths.append(p)
            if len(paths) >= 6:
                break
    if not paths and main.exists() and await asyncio.to_thread(has_alpha, main):
        paths = [main]
    return paths


# 「模特上身」套图的附加信息（品类、尺寸、材质、模特、面料）；旧丝巾套图存在 plan.scarf；通用商品返回 None
def scarf_info(ecom: EcomSet) -> dict | None:
    plan = ecom.plan or {}
    info = plan.get("styled") or plan.get("scarf")
    return info if isinstance(info, dict) else None


# 套图当前的字体风格（旧套图没存时按品类与场景给默认）；信息屏本身就是文字，用 _type_style
def type_style_of(ecom: EcomSet) -> str:
    """整套图的字体风格；选了「不加文字」时返回 none（成图只保留画面）。"""
    plan = ecom.plan or {}
    if (plan.get("styled") or {}).get("no_text") or plan.get("no_text"):
        return "none"
    return _type_style(ecom)


def _type_style(ecom: EcomSet) -> str:
    from app.services.ecom_set import typo

    info = scarf_info(ecom) or {}
    default = typo.default_type_style(str(info.get("category") or "general"), info.get("scene"))
    return typo.normalize_type_style(info.get("type_style") or (ecom.plan or {}).get("type_style"), default)


# 按丝巾平铺图自动校色（只修正接近丝巾主色的像素）；未校色时原样返回
def color_fix(path: Path, product: Path) -> Path:
    from app.services.color_match import match_colors_file

    out = path.with_name(path.stem + "_cm.jpg")
    try:
        stats = match_colors_file(path, product, out)
    except Exception:  # noqa: BLE001
        logger.warning("丝巾校色失败，使用原图 %s", path.name, exc_info=True)
        return path
    return out if stats.get("applied") and out.exists() else path


# 不调模型的槽位（尺寸图、平铺图）：直接用商品图排版，返回成图本地路径
def render_flat_item(ecom: EcomSet, item: EcomSetItem) -> Path:
    from app.services.ecom_set import scarf as scarf_mod

    out = set_dir(ecom.user_id, ecom.id) / f"final_{item.idx:02d}_{uuid.uuid4().hex[:6]}.jpg"
    product = Path(ecom.product_image_path)
    copy = item.copy_json or {}
    size = output_size(ecom.platform, item.slot)
    if item.slot == "scarf_size":
        return scarf_mod.render_size_spec(product, out, size_id=(scarf_info(ecom) or {}).get("size"), copy=copy, size=size)
    if item.slot == "scarf_crop":
        return scarf_mod.render_crop(product, out, copy=copy, size=size)
    if SLOTS[item.slot].ref == "info":
        from app.services.ecom_set.infoscreens import render_info

        info = dict(scarf_info(ecom) or {})
        info.setdefault("product_path", str(product))
        info.setdefault("product_name", ecom.name)
        return render_info(item.slot, out, size=size, copy=copy, styled=info, type_style=_type_style(ecom))
    if item.slot.startswith("goods_"):
        from app.services.ecom_set import composite

        cut = composite.load_cutout(product) if composite.has_alpha(product) else composite.flatten_white(product).convert("RGBA")
        composite.render_white(cut, size).save(out, "JPEG", quality=94)
        return out
    return scarf_mod.render_flat(product, out, copy=copy, size=size)


# 把底图排版成成图，返回 (本地路径, 对外 URL)
async def render_final(ecom: EcomSet, item: EcomSetItem, base: Path) -> tuple[Path, str]:
    if SLOTS.get(item.slot) is not None and SLOTS[item.slot].ref in FREE_REFS:
        out = await asyncio.to_thread(render_flat_item, ecom, item)
        return out, await asyncio.to_thread(public_url, out)
    out = set_dir(ecom.user_id, ecom.id) / f"final_{item.idx:02d}_{uuid.uuid4().hex[:6]}.jpg"
    if SLOTS.get(item.slot) is not None and SLOTS[item.slot].ref == "ingredients":
        # 成分图本身就是文字排版：不受「不加文字」影响
        from app.services.ecom_set import ingredientscreen

        await asyncio.to_thread(ingredientscreen.render, base, out, size=output_size(ecom.platform, item.slot),
                                copy=item.copy_json, styled=scarf_info(ecom) or {}, type_style=_type_style(ecom))
        return out, await asyncio.to_thread(public_url, out)
    await asyncio.to_thread(
        render,
        base,
        out,
        size=output_size(ecom.platform, item.slot),
        layout=item.layout,
        blank=item.blank,
        copy=item.copy_json,
        type_style=type_style_of(ecom),
    )
    return out, await asyncio.to_thread(public_url, out)


# 从任务 payload 取 (set_id, idx)；idx 可以是 0，不能用 `or` 兜底
def parse_target(payload: dict) -> tuple[int, int]:
    set_id = int(payload.get("set_id") or 0)
    raw = payload.get("idx")
    return set_id, (int(raw) if raw is not None else -1)


async def run_item_job(task: TaskRun) -> dict[str, Any]:
    """任务平台 handler：为套图中的一张生成底图并排版。失败时标记该张失败并抛出（不扣费）。"""
    from app.database import AsyncSessionLocal

    payload = task.payload or {}
    set_id, idx = parse_target(payload)
    async with AsyncSessionLocal() as db:
        item = (
            await db.execute(
                select(EcomSetItem).where(EcomSetItem.set_id == set_id, EcomSetItem.idx == idx)
            )
        ).scalar_one_or_none()
        ecom = await db.get(EcomSet, set_id)
        if item is None or ecom is None:
            raise ValueError("套图或图片不存在")
        if item.task_run_id and int(item.task_run_id) != int(task.id):
            return {"ok": True, "skipped": "superseded"}
        item.status = "running"
        item.error = None
        await db.commit()
        try:
            base = await generate_base(db, ecom, item)
            item.base_path = str(base)
            item.base_url = await asyncio.to_thread(public_url, base)
            _, final_url = await render_final(ecom, item, base)
            item.final_url = final_url
            item.status = "succeeded"
            await refresh_set_status(db, set_id)
            await db.commit()
            return {"ok": True, "set_id": set_id, "idx": idx, "url": final_url}
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            item = await db.get(EcomSetItem, item.id)
            if item is not None:
                item.status = "failed"
                item.error = str(exc)[:500] or "生成失败"
            await refresh_set_status(db, set_id)
            await db.commit()
            logger.exception("ecom set item failed set=%s idx=%s", set_id, idx)
            raise
