"""通用「图片列表 → 带货项目/报价」入口，套图与批量出图共用。"""
from app.config import get_settings
from app.models import Project, ProjectStatus, Shot
from app.services.daihuo.motion import motion_unavailable_reason, normalize_form, pipeline_mode_for
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID

ALLOWED_RATIOS = {"9:16", "16:9", "1:1", "4:3"}


# 将已校验图片列表对应的商品资料建为草稿，不创建生图任务或自动写稿。
async def create_image_project(db, user, *, name: str, notes: str, product: dict, items: list[dict],
                               output_ratio: str, voice_id: str, ecom_set_id=None, request_id=None,
                               video_form: str = "static") -> Project:
    if not items:
        raise ValueError("还没有生成好的图片")
    form = normalize_form(video_form)
    if form != "static" and (reason := motion_unavailable_reason()):
        raise ValueError(reason)
    points = "；".join(product.get("selling_points") or [])
    source = "\n".join(value for value in [f"商品：{name}", points and f"卖点：{points}", notes] if value)
    project = Project(user_id=user.id, template_id=DAIHUO_TEMPLATE_ID, title=name[:60] or "商品带货",
                      source_type="script", source_text=source or name, resolution_mode="preview",
                      pipeline_mode=pipeline_mode_for(form), daihuo_video_form=form, output_ratio=output_ratio if output_ratio in ALLOWED_RATIOS else "9:16",
                      voice_id=(voice_id or "").strip(), ecom_set_id=ecom_set_id,
                      daihuo_request_id=request_id, status=ProjectStatus.DRAFT)
    db.add(project)
    await db.flush()
    return project


# 已有图片按写稿、配音阶段报价，不重复计入生图费用；动态展示另估扩图与视频。无数据库写入。
async def quote_images(user, items: list[dict], *, output_ratio: str, voice_id: str, video_form: str = "static") -> dict:
    from app.services.billing.estimates import estimate_phase_fen
    from app.services.billing.settlement import billing_active
    from app.services.tokenfree_pricing import ensure_official_rates

    if not items:
        raise ValueError("还没有生成好的图片")
    ratio = output_ratio if output_ratio in ALLOWED_RATIOS else "9:16"
    voice = (voice_id or "").strip()
    form = normalize_form(video_form)
    probe = Project(pipeline_mode="image_text", output_ratio=ratio, voice_id=voice,
                    shots=[Shot(image_url=item["base_path"], narration="", duration=8) for item in items])
    settings = get_settings()
    script = tts = video = image = 0
    motion_shots = motion_shot_count(len(items), form)
    if billing_active(user, settings):
        if not settings.ark_mock:
            await ensure_official_rates(settings)
        script = estimate_phase_fen(probe, "script", settings)
        tts = estimate_phase_fen(probe, "assets", settings)
        if motion_shots:
            video, image = quote_motion_fen(items[:motion_shots], ratio=ratio, settings=settings)
    reason = motion_unavailable_reason() if form != "static" else None
    return {"output_ratio": ratio, "voice_id": voice, "video_form": form, "usable_images": len(items),
            "motion_shots": motion_shots, "image_fen": image, "script_fen": script, "tts_fen": tts,
            "video_fen": video, "estimate_fen": script + tts + video + image,
            "motion_available": motion_unavailable_reason() is None, "motion_unavailable_reason": reason or ""}


# 报价时的动态镜头数：混合按上限估（实际以写稿后模型建议为准，只会更少），全动态为全部
def motion_shot_count(total: int, form: str) -> int:
    if form == "motion":
        return total
    if form == "mixed":
        return min(total, max(1, int(get_settings().daihuo_motion_max_shots)))
    return 0


# 动态镜头视频费 + 可能的扩图费（画幅差太多需扩图的才计）；按 8 秒口播上限保守估
def quote_motion_fen(items: list[dict], *, ratio: str, settings) -> tuple[int, int]:
    from pathlib import Path

    from PIL import Image

    from app.services.billing.estimates import _catalog_image_fen, estimate_phase_fen
    from app.services.daihuo.motion import CROP_MIN_KEEP, crop_keep_ratio

    outpaint = 0
    shots = []
    for item in items:
        try:
            with Image.open(Path(item["base_path"])) as im:
                keep = crop_keep_ratio(im.size, ratio)
        except OSError:
            keep = 0.0
        needs = keep < CROP_MIN_KEEP
        outpaint += int(needs)
        # 能直接裁成满幅的镜头用满幅首帧占位，视频阶段估价就不会再算扩图
        shots.append(Shot(image_url="/static/daihuo/frame_probe.jpg" if needs else "/static/daihuo/motion_probe.jpg",
                          narration="", duration=8))
    probe = Project(pipeline_mode="full", output_ratio=ratio, resolution_mode="preview",
                    template_id=DAIHUO_TEMPLATE_ID, shots=shots)
    total = estimate_phase_fen(probe, "videos", settings)
    image = outpaint * _catalog_image_fen(settings)
    return max(0, total - image), image


# 在项目行锁内只启动写稿；重复入口或已有分镜绝不推进到配音收费阶段。
async def start_image_project_script(db, user, project_id: int) -> Project:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models_ecom import EcomJobVideo
    from app.models_tasks import TaskRun
    from app.schemas_tasks import TaskCreateRequest, TaskTargetBind
    from app.services.tasks.service import ACTIVE_TASK_STATUSES, create_task

    project = (await db.execute(select(Project).where(Project.id == project_id, Project.user_id == user.id)
                               .options(selectinload(Project.shots)).with_for_update()
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if project is None or not (project.ecom_set_id or await db.get(EcomJobVideo, project_id)):
        raise LookupError("项目不存在")
    active = await db.scalar(select(TaskRun.id).where(TaskRun.project_id == project_id,
                             TaskRun.status.in_(ACTIVE_TASK_STATUSES)).limit(1))
    if project.shots or active:
        return project
    project.error_msg = None
    project.status, project.progress = ProjectStatus.SCRIPTING, 1
    await create_task(db, user, TaskCreateRequest(
        domain="kepu", task_type="project_pipeline", project_id=project_id,
        dedupe_key=f"kepu:project_pipeline:{project_id}:0:script",
        payload={"project_id": project_id, "restart": False, "phase": "script",
                 "pipeline_mode": project.pipeline_mode or "image_text"},
        targets=[TaskTargetBind(target_type="project", target_id=project_id)],
    ))
    return project
