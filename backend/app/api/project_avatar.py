"""科普「数字人口播」项目接口：上传人脸照、生成定妆照、重画单段场景、查看服务与可选模型。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import _get_owned_project
from app.database import get_db
from app.deps import get_current_user
from app.models import Project, ProjectStatus, User
from app.schemas import ProjectOut
from app.services import avatar_ai, avatar_video, storage
from app.services.kepu_avatar_look import DEFAULT_LOOK_STYLE, LOOK_STYLES

router = APIRouter(tags=["projects"])

_ALLOWED = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_BUSY = {ProjectStatus.SCRIPTING, ProjectStatus.IMAGING, ProjectStatus.VIDEOING, ProjectStatus.AUDIOING,
         ProjectStatus.COMPOSING, ProjectStatus.AUDITING}
_MAX_BYTES = 10 * 1024 * 1024


@router.post("/projects/{project_id}/avatar-image", response_model=ProjectOut)
async def upload_avatar_image(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """上传人脸照（只取脸）；在生成定妆照之前直接当形象用。换照片后场景图与对口型视频全部作废。"""
    project = await _get_owned_project(db, project_id, user)
    if project.status in _BUSY:
        raise HTTPException(status_code=409, detail="生成进行中，无法更换照片")
    ext = _ALLOWED.get((file.content_type or "").lower())
    if not ext:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WebP")
        ext = ".jpg" if suffix == ".jpeg" else suffix
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="照片不能超过 10MB")
    # 文件名带版本，避免浏览器 / CDN 缓存旧照片
    version = sum(1 for _ in storage.project_dir(project_id).glob("avatar_portrait_*")) + 1
    dest = storage.project_dir(project_id) / f"avatar_portrait_{version}{ext}"
    dest.write_bytes(raw)
    project.avatar_image_url = storage.publish_local(dest)
    project.avatar_face_url = project.avatar_image_url
    project.avatar_look_style = ""
    if not project.cover_url:
        project.cover_url = project.avatar_image_url
    for shot in project.shots or []:
        shot.image_url = project.avatar_image_url
        shot.video_url = None
        shot.avatar_job_id = None
    project.final_video_url = None
    await db.commit()
    return await _get_owned_project(db, project_id, user)


class LookIn(BaseModel):
    """生成定妆照：风格 id 与改图模型（空则用项目/后台默认）。"""

    style: str = Field(default=DEFAULT_LOOK_STYLE, max_length=32)
    model: str = Field(default="", pattern=r"^[A-Za-z0-9._:/-]{0,64}$")


def _ai_error(exc: Exception) -> HTTPException:
    """改图失败转成 502 中文短句；未配置转 400。"""
    if isinstance(exc, avatar_ai.AvatarAINotConfigured):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc)[:300] or "生成失败，请稍后重试")


@router.post("/projects/{project_id}/avatar-look", response_model=ProjectOut)
async def generate_avatar_look(
    project_id: int,
    body: LookIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """用人脸照生成定妆照（新服装/姿势/纯色背景，只保留脸），并记住所选模型；约 10–60 秒。"""
    from app.services.kepu_avatar_look import generate_look

    project = await _get_owned_project(db, project_id, user)
    if project.status in _BUSY:
        raise HTTPException(status_code=409, detail="生成进行中，无法更换形象")
    if body.model:
        project.avatar_image_model = body.model
        await db.commit()
    try:
        await generate_look(project_id, body.style, body.model or None)
    except Exception as exc:  # noqa: BLE001
        raise _ai_error(exc) from exc
    db.expire_all()
    return await _get_owned_project(db, project_id, user)


@router.post("/projects/{project_id}/shots/{shot_id}/avatar-scene", response_model=ProjectOut)
async def regen_avatar_scene(
    project_id: int,
    shot_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    """按该段场景描述重画场景图（同一个人），该段对口型视频随之作废。"""
    from app.services.kepu_avatar import portrait_path
    from app.services.kepu_avatar_look import ensure_scene_images

    project = await _get_owned_project(db, project_id, user)
    if project.status in _BUSY:
        raise HTTPException(status_code=409, detail="生成进行中，请稍后再试")
    shot = next((s for s in project.shots if s.id == shot_id), None)
    if not shot:
        raise HTTPException(status_code=404, detail="分镜不存在")
    if not (shot.img_prompt or "").strip():
        raise HTTPException(status_code=400, detail="该段没有场景描述，请先填写")
    shot.image_url = project.avatar_image_url
    project.final_video_url = None
    await db.commit()
    try:
        await ensure_scene_images(project_id, await portrait_path(project), only_shot_ids={shot_id})
    except Exception as exc:  # noqa: BLE001
        raise _ai_error(exc) from exc
    if project.status in {ProjectStatus.VIDEO_READY, ProjectStatus.AUDITING, ProjectStatus.DONE,
                          ProjectStatus.REJECTED, ProjectStatus.FAILED}:
        from app.services.kepu_avatar import _after_regen

        await _after_regen(project_id)
    db.expire_all()
    return await _get_owned_project(db, project_id, user)


@router.get("/avatar/options")
async def avatar_options(user: User = Depends(get_current_user)) -> dict:
    """创建/编辑页用：定妆照风格、可选改图模型、后台默认模型、素材服务是否已配置。"""
    return {
        "ai_configured": avatar_ai.is_configured(),
        "default_image_model": avatar_ai.resolve_image_model(None),
        "image_models": avatar_ai.IMAGE_MODELS,
        "look_styles": [{"id": k, "label": v[0]} for k, v in LOOK_STYLES.items()],
    }


@router.get("/avatar/status")
async def avatar_status(user: User = Depends(get_current_user)) -> dict:
    """数字人服务是否已配置、能否连通（创建页提示用，不返回地址等敏感信息）。"""
    if avatar_video.is_mock():
        return {"configured": True, "online": True, "mock": True, "engines": list(avatar_video.ENGINES)}
    try:
        info = await avatar_video.health()
    except avatar_video.AvatarNotConfigured:
        return {"configured": False, "online": False, "mock": False, "engines": list(avatar_video.ENGINES)}
    except Exception:  # noqa: BLE001
        return {"configured": True, "online": False, "mock": False, "engines": list(avatar_video.ENGINES)}
    return {"configured": True, "online": True, "mock": False, "engines": info.get("engines") or [],
            "active_engine": info.get("active_engine"), "queued": info.get("queued", 0)}
