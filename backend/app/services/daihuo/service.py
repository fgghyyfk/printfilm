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
from app.models_ecom import EcomJobVideo, EcomSet
from app.services import storage
from app.services.daihuo.frames import pad_to_frame
from app.services.daihuo.images import ALLOWED_RATIOS, create_image_project, quote_images
from app.services.daihuo.script import DAIHUO_SYSTEM, build_user_prompt, mock_segments, parse_segments
from app.services.ecom_set.presets import slot_label

logger = logging.getLogger(__name__)


async def create_project_from_set(
    db: AsyncSession, user: User, set_id: int, *, output_ratio: str = "9:16", voice_id: str = "",
    create_new: bool = False, request_id: str | None = None,
) -> Project:
    """用一套已生成的套图建「商品带货」静图口播项目（status=DRAFT，前端随后调 generate 写稿）。"""
    ecom = (
        await db.execute(select(EcomSet).where(EcomSet.id == set_id, EcomSet.user_id == user.id)
                             .with_for_update().execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if ecom is None:
        raise LookupError("套图不存在")
    ratio = output_ratio if output_ratio in ALLOWED_RATIOS else "9:16"
    voice = (voice_id or "").strip()
    if create_new:
        if not request_id:
            raise ValueError("请重新选择生成选项后再试")
        requested = (await db.execute(select(Project).where(
            Project.user_id == user.id, Project.ecom_set_id == ecom.id,
            Project.daihuo_request_id == request_id,
        ))).scalar_one_or_none()
        if requested is not None:
            if requested.output_ratio != ratio or requested.voice_id != voice:
                raise ValueError("请重新选择生成选项后再试")
            return requested
    elif ecom.project_id:
        existing = await db.get(Project, ecom.project_id)
        if existing is not None and existing.user_id == user.id:
            return existing
    if not usable_items(ecom):
        raise ValueError("套图还没有生成好的图片")
    project = await create_image_project(
        db, user, name=ecom.name, notes=ecom.notes or "", product=(ecom.plan or {}).get("product") or {},
        items=usable_items(ecom), output_ratio=ratio, voice_id=voice, ecom_set_id=ecom.id,
        request_id=request_id if create_new else None,
    )
    # 兼容单指针入口：指向最近创建的视频，旧项目与作品仍各自保留。
    ecom.project_id = project.id
    await db.commit()
    return project


# 项目是否有关联的电商图片来源（保留旧函数名兼容流水线入口）
async def project_has_ecom_set(project_id: int) -> bool:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        return bool(project and (project.ecom_set_id or await db.get(EcomJobVideo, project_id)))


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
        if project.ecom_set_id:
            ecom = await db.get(EcomSet, project.ecom_set_id)
            if ecom is None or ecom.user_id != project.user_id:
                raise ValueError("关联的套图不存在")
            items = usable_items(ecom)
        else:
            from types import SimpleNamespace

            source = await db.get(EcomJobVideo, project_id)
            if source is None:
                raise ValueError("关联的图片不存在")
            ecom = SimpleNamespace(name=source.name, notes=source.notes, plan={})
            items = [item for item in source.frames if Path(item["base_path"]).is_file()]
            if len(items) != len(source.frames):
                raise ValueError("图片无法识别，请换一张")
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
    logger.info("daihuo shots built project=%s shots=%d", project_id, len(items))
    return len(items)


async def quote_project_from_set(db: AsyncSession, user: User, set_id: int, *, output_ratio: str = '9:16', voice_id: str = '') -> dict:
    """只读套图归属与可用底图；沿现有阶段估价，不创建项目、任务或冻结。"""
    ecom = (await db.execute(select(EcomSet).where(EcomSet.id == set_id, EcomSet.user_id == user.id))).scalar_one_or_none()
    if ecom is None:
        raise LookupError("套图不存在")
    return await quote_images(user, usable_items(ecom), output_ratio=output_ratio, voice_id=voice_id)
