"""科普「数字人口播」项目接口：上传半身照、查看数字人服务是否可用。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import _get_owned_project
from app.database import get_db
from app.deps import get_current_user
from app.models import Project, ProjectStatus, User
from app.schemas import ProjectOut
from app.services import avatar_video, storage

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
    """上传数字人半身照；换照片后已生成的对口型视频全部作废。"""
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
    if not project.cover_url:
        project.cover_url = project.avatar_image_url
    for shot in project.shots or []:
        shot.image_url = project.avatar_image_url
        shot.video_url = None
        shot.avatar_job_id = None
    project.final_video_url = None
    await db.commit()
    return await _get_owned_project(db, project_id, user)


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
