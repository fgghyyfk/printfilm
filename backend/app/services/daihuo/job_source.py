"""批量出图转带货：用户隔离、最少两张成功图、独立素材快照与请求幂等。"""
import asyncio
import shutil

from sqlalchemy import select

from app.models import Project
from app.models_ecom import EcomJob, EcomJobVideo
from app.services import storage
from app.services.daihuo.images import ALLOWED_RATIOS, create_image_project, quote_images
from app.services.daihuo.motion import normalize_form
from app.services.ecom_jobs.sources import item_result_path


# 锁定归属当前用户的来源；与重试/完成状态更新互斥。
async def owned_job(db, user, job_id: int, *, lock=False):
    query = select(EcomJob).where(EcomJob.id == job_id, EcomJob.user_id == user.id)
    if lock:
        query = query.with_for_update()
    job = (await db.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()
    if job is None:
        raise LookupError("任务不存在")
    return job


# 读取创建时可用的成功图，至少两张；缺失素材不会被算作可用分镜。
async def job_images(user, job) -> list[dict]:
    items = []
    for item in job.items:
        if item.status != "succeeded" or not item.result_url:
            continue
        path = await item_result_path(user, job.id, item)
        items.append({"idx": item.idx, "slot": "image", "slot_label": f"商品图 {item.idx + 1}",
                      "scene": "", "title": "", "subtitle": "", "bullets": [], "base_path": str(path)})
    if len(items) < 2:
        raise ValueError("请至少生成两张成功图片")
    return items


# 返回最近关联项目，前端据此提供继续入口而非重复写稿。
async def latest_project(db, user, job_id: int) -> Project | None:
    return (await db.execute(select(Project).join(EcomJobVideo, EcomJobVideo.project_id == Project.id)
                             .where(EcomJobVideo.job_id == job_id, Project.user_id == user.id)
                             .order_by(Project.id.desc()).limit(1))).scalar_one_or_none()


# 已成功图片的只读报价不创建任务、项目或冻结；mock 不访问网络。
async def quote_project_from_job(db, user, job_id: int, *, output_ratio="9:16", voice_id="", video_form="static") -> dict:
    job = await owned_job(db, user, job_id)
    return await quote_images(user, await job_images(user, job), output_ratio=output_ratio, voice_id=voice_id,
                              video_form=video_form)


# 建项目时复制素材快照；同一请求只建一个项目，另一条视频必须显式新建。
async def create_project_from_job(db, user, job_id: int, *, output_ratio="9:16", voice_id="",
                                  create_new=False, request_id=None, name="", notes="", video_form="static") -> Project:
    job = await owned_job(db, user, job_id, lock=True)
    ratio = output_ratio if output_ratio in ALLOWED_RATIOS else "9:16"
    voice = (voice_id or "").strip()
    if create_new and not request_id:
        raise ValueError("请重新选择生成选项后再试")
    if create_new:
        existing = (await db.execute(select(Project).join(EcomJobVideo, EcomJobVideo.project_id == Project.id)
                                    .where(EcomJobVideo.job_id == job.id, EcomJobVideo.request_id == request_id,
                                           Project.user_id == user.id))).scalar_one_or_none()
        if existing:
            snapshot = await db.get(EcomJobVideo, existing.id)
            requested_name = (name or job.title or "").strip()
            requested_notes = (notes or "").strip()
            if (existing.output_ratio != ratio or existing.voice_id != voice
                    or (existing.daihuo_video_form or "static") != normalize_form(video_form)
                    or snapshot.name != requested_name or snapshot.notes != requested_notes):
                raise ValueError("请重新选择生成选项后再试")
    else:
        existing = await latest_project(db, user, job.id)
    if existing:
        return existing
    items = await job_images(user, job)
    name = (name or job.title or "").strip()
    if not name:
        raise ValueError("请填写品名")
    notes = (notes or "").strip()
    project = await create_image_project(db, user, name=name, notes=notes, product={}, items=items,
                                         output_ratio=ratio, voice_id=voice, video_form=video_form)
    directory = storage.project_dir(project.id) / "daihuo" / "sources"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for item in items:
            dest = directory / f"image_{item['idx']}.png"
            await asyncio.to_thread(shutil.copyfile, item["base_path"], dest)
            item["base_path"] = str(dest)
        db.add(EcomJobVideo(project_id=project.id, job_id=job.id, request_id=request_id if create_new else "initial",
                            name=name, notes=notes, frames=items))
        await db.commit()
    except (Exception, asyncio.CancelledError):
        await db.rollback()
        await asyncio.to_thread(shutil.rmtree, directory, True)
        raise
    return project
