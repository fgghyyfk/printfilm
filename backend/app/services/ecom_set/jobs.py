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
from app.services.ecom_set.presets import GEN_SIZE, SLOTS, output_size
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
        from app.services.ecom_set import scarf as scarf_mod

        prompt = scarf_mod.build_prompt(slot_prompt=slot.prompt, scene=item.scene, ref=slot.ref, size=scarf.get("size"))
        refs = [scarf["model_url"], ecom.product_image_url] if slot.ref == "model" and scarf.get("model_url") \
            else [ecom.product_image_url]
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
    )
    await record_seedream_image_usage(
        db,
        user_id=ecom.user_id,
        model=settings.model_image,
        domain="studio",
        image_result=result,
        extra_raw={"tool_id": "ecomset", "set_id": ecom.id, "idx": item.idx},
    )
    url = result.local_url or result.remote_url or ""
    if not url:
        raise RuntimeError("生图未返回图片")
    path = await storage.ensure_local_media(url, dest)
    if scarf is not None:
        path = await asyncio.to_thread(color_fix, path, product)
    return path


# 丝巾详情页的附加信息（尺寸、模特）；通用商品返回 None
def scarf_info(ecom: EcomSet) -> dict | None:
    info = (ecom.plan or {}).get("scarf")
    return info if isinstance(info, dict) else None


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
    if item.slot == "scarf_size":
        return scarf_mod.render_size_spec(product, out, size_id=(scarf_info(ecom) or {}).get("size"), copy=copy)
    return scarf_mod.render_flat(product, out, copy=copy)


# 把底图排版成成图，返回 (本地路径, 对外 URL)
async def render_final(ecom: EcomSet, item: EcomSetItem, base: Path) -> tuple[Path, str]:
    if SLOTS.get(item.slot) is not None and SLOTS[item.slot].ref == "flat":
        out = await asyncio.to_thread(render_flat_item, ecom, item)
        return out, await asyncio.to_thread(public_url, out)
    out = set_dir(ecom.user_id, ecom.id) / f"final_{item.idx:02d}_{uuid.uuid4().hex[:6]}.jpg"
    await asyncio.to_thread(
        render,
        base,
        out,
        size=output_size(ecom.platform, item.slot),
        layout=item.layout,
        blank=item.blank,
        copy=item.copy_json,
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
