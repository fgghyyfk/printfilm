"""通用「图片列表 → 带货项目/报价」入口，套图与批量出图共用。"""
from app.config import get_settings
from app.models import Project, ProjectStatus, Shot
from app.services.templates_seed_daihuo import DAIHUO_TEMPLATE_ID

ALLOWED_RATIOS = {"9:16", "16:9", "1:1", "4:3"}


# 将已校验图片列表对应的商品资料建为草稿，不创建生图任务或自动写稿。
async def create_image_project(db, user, *, name: str, notes: str, product: dict, items: list[dict],
                               output_ratio: str, voice_id: str, ecom_set_id=None, request_id=None) -> Project:
    if not items:
        raise ValueError("还没有生成好的图片")
    points = "；".join(product.get("selling_points") or [])
    source = "\n".join(value for value in [f"商品：{name}", points and f"卖点：{points}", notes] if value)
    project = Project(user_id=user.id, template_id=DAIHUO_TEMPLATE_ID, title=name[:60] or "商品带货",
                      source_type="script", source_text=source or name, resolution_mode="preview",
                      pipeline_mode="image_text", output_ratio=output_ratio if output_ratio in ALLOWED_RATIOS else "9:16",
                      voice_id=(voice_id or "").strip(), ecom_set_id=ecom_set_id,
                      daihuo_request_id=request_id, status=ProjectStatus.DRAFT)
    db.add(project)
    await db.flush()
    return project


# 已有图片按写稿、配音阶段报价，不重复计入生图费用；无数据库写入。
async def quote_images(user, items: list[dict], *, output_ratio: str, voice_id: str) -> dict:
    from app.services.billing.estimates import estimate_phase_fen
    from app.services.billing.settlement import billing_active
    from app.services.tokenfree_pricing import ensure_official_rates

    if not items:
        raise ValueError("还没有生成好的图片")
    ratio = output_ratio if output_ratio in ALLOWED_RATIOS else "9:16"
    voice = (voice_id or "").strip()
    probe = Project(pipeline_mode="image_text", output_ratio=ratio, voice_id=voice,
                    shots=[Shot(image_url=item["base_path"], narration="", duration=8) for item in items])
    settings = get_settings()
    script = tts = 0
    if billing_active(user, settings):
        if not settings.ark_mock:
            await ensure_official_rates(settings)
        script = estimate_phase_fen(probe, "script", settings)
        tts = estimate_phase_fen(probe, "assets", settings)
    return {"output_ratio": ratio, "voice_id": voice, "usable_images": len(items), "image_fen": 0,
            "script_fen": script, "tts_fen": tts, "estimate_fen": script + tts}


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
        payload={"project_id": project_id, "restart": False, "phase": "script", "pipeline_mode": "image_text"},
        targets=[TaskTargetBind(target_type="project", target_id=project_id)],
    ))
    return project
