"""商品带货编排：由套图建带货项目、写稿阶段按套图建分镜（画面复用套图底图，不再出图）。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Project, ProjectStatus, Shot, ShotStatus, User
from app.models_ecom import EcomSet
from app.services import storage
from app.services.daihuo.frames import pad_to_frame
from app.services.daihuo.script import DAIHUO_SYSTEM, build_user_prompt, mock_segments, parse_segments
from app.services.ecom_set.presets import slot_label
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID

logger = logging.getLogger(__name__)
ALLOWED_RATIOS = {"9:16", "16:9", "1:1", "4:3"}


async def create_project_from_set(
    db: AsyncSession, user: User, set_id: int, *, output_ratio: str = "9:16", voice_id: str = "",
) -> Project:
    """用一套已生成的套图建「商品带货」静图口播项目（status=DRAFT，前端随后调 generate 写稿）。"""
    ecom = (
        await db.execute(select(EcomSet).where(EcomSet.id == set_id, EcomSet.user_id == user.id))
    ).scalar_one_or_none()
    if ecom is None:
        raise LookupError("套图不存在")
    if not any(it.status == "succeeded" and it.base_path for it in ecom.items):
        raise ValueError("套图还没有生成好的图片")
    if ecom.project_id:
        existing = await db.get(Project, ecom.project_id)
        if existing is not None and existing.user_id == user.id:
            return existing
    product = (ecom.plan or {}).get("product") or {}
    points = "；".join(product.get("selling_points") or [])
    source = "\n".join(x for x in [f"商品：{ecom.name}", points and f"卖点：{points}", ecom.notes] if x)
    project = Project(
        user_id=user.id, template_id=DAIHUO_TEMPLATE_ID, title=ecom.name[:60] or "商品带货",
        source_type="script", source_text=source or ecom.name, resolution_mode="preview",
        pipeline_mode="image_text",
        output_ratio=output_ratio if output_ratio in ALLOWED_RATIOS else "9:16",
        voice_id=(voice_id or "").strip(), ecom_set_id=ecom.id, status=ProjectStatus.DRAFT,
    )
    db.add(project)
    await db.flush()
    ecom.project_id = project.id
    await db.commit()
    return project


# 项目是否关联了套图（写稿阶段据此走带货分支）
async def project_has_ecom_set(project_id: int) -> bool:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        return bool(project and project.ecom_set_id)


# 套图里可用作画面的各张（已成功、有底图），转成写稿用的字典
def usable_items(ecom: EcomSet) -> list[dict]:
    out = []
    for it in ecom.items:
        if it.status != "succeeded" or not it.base_path or not Path(it.base_path).exists():
            continue
        copy = it.copy_json or {}
        out.append({
            "idx": it.idx, "slot": it.slot, "slot_label": slot_label(it.slot), "scene": it.scene or "",
            "title": copy.get("title") or "", "subtitle": copy.get("subtitle") or "",
            "bullets": list(copy.get("bullets") or []), "base_path": it.base_path,
        })
    return out


# 调 LLM 写分段口播（ARK_MOCK 时用固定口播）
async def write_segments(ecom: EcomSet, items: list[dict]) -> dict:
    if get_settings().ark_mock:
        return mock_segments(items, name=ecom.name)
    from app.services.llm_client import chat_completions

    prompt = build_user_prompt(
        name=ecom.name, notes=ecom.notes or "", product=(ecom.plan or {}).get("product") or {}, items=items
    )
    raw = await chat_completions(DAIHUO_SYSTEM, prompt, temperature=0.7, max_tokens=4096, timeout=180.0)
    return parse_segments(raw, items, name=ecom.name)


async def build_daihuo_shots(project_id: int) -> int:
    """写稿阶段的带货分支：按套图写口播并建分镜，图直接用套图底图（补成视频画幅）。返回分镜数。"""
    from app.database import AsyncSessionLocal
    from app.services.pipeline import _project_output_ratio, _record_usage_est
    from app.services.seedance_segments import estimate_narration_duration

    async with AsyncSessionLocal() as db:
        project = (
            await db.execute(
                select(Project).where(Project.id == project_id)
                .options(selectinload(Project.template), selectinload(Project.shots))
            )
        ).scalar_one()
        ecom = await db.get(EcomSet, project.ecom_set_id)
        if ecom is None:
            raise ValueError("关联的套图不存在")
        items = usable_items(ecom)
        if not items:
            raise ValueError("套图还没有生成好的图片")
        ratio = _project_output_ratio(project)

    script = await write_segments(ecom, items)
    seg_by_idx = {s["idx"]: s for s in script["segments"]}

    async with AsyncSessionLocal() as db:
        project = (
            await db.execute(
                select(Project).where(Project.id == project_id).options(selectinload(Project.shots))
            )
        ).scalar_one()
        from app.services.kepu_shot_edits import clear_storyboard_rows

        await clear_storyboard_rows(db, project)
        out_dir = storage.project_dir(project_id) / "daihuo"
        for no, it in enumerate(items, start=1):
            seg = seg_by_idx.get(it["idx"]) or {}
            narration = seg.get("narration") or ""
            frame = out_dir / f"frame_{no:02d}_{uuid.uuid4().hex[:6]}.jpg"
            await asyncio.to_thread(pad_to_frame, Path(it["base_path"]), frame, ratio=ratio)
            image_url = storage.rel_static_url(frame)
            db.add(Shot(
                project_id=project_id, shot_no=no,
                duration=float(max(3, min(10, estimate_narration_duration(narration)))),
                narration=narration, overlay_title=seg.get("title", it["title"]),
                compliance_changes=seg.get("compliance_changes") or {},
                overlay_subtitle=it["subtitle"], img_prompt=it["scene"][:500],
                video_prompt="", segment_script="", camera="slow push in",
                image_url=image_url, status=ShotStatus.IMAGE_READY,
            ))
        if project.title in {"", "商品带货", ecom.name[:60]} and script.get("title"):
            project.title = script["title"][:60]
            project.compliance_changes = script.get("compliance_changes") or {}
        project.status = ProjectStatus.SCRIPT_READY
        project.progress = 15
        await db.commit()
    if not get_settings().ark_mock:
        s = get_settings()
        await _record_usage_est(project_id, "llm_chat", tokens=4000, model=s.model_llm)
    logger.info("daihuo shots built project=%s set=%s shots=%d", project_id, ecom.id, len(items))
    return len(items)
