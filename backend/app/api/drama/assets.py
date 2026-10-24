"""Drama asset endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.models_drama import DramaAsset, DramaProject
from app.schemas_drama import (
    DramaActivateImageVersionRequest,
    DramaAssetCreate,
    DramaAssetOut,
    DramaAssetUpdate,
    SeedAssetsFromScriptOut,
)
from app.services.drama.access import get_owned_asset, get_owned_drama_project
from app.services.teams.write_access import workspace_team
from app.services.billing import run_billed_ephemeral
from app.services.billing.http import http_exception_for_value_error
from app.services.drama.billing_util import record_seed_assets_llm_usage
from app.services.drama.jobs import dispatch_seed_assets_job
from app.services.drama.seed import (
    _asset_dedupe_key,
    _normalize_asset_name,
    purge_voice_like_character_assets,
    seed_assets_from_script,
)

router = APIRouter()


@router.get("/assets", response_model=list[DramaAssetOut])
async def list_assets(
    project_id: int | None = None,
    library_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    scope_team_id: int | None = Depends(workspace_team),
) -> list[DramaAssetOut]:
    # Global library = all assets of user's drama projects; optional project filter
    q = (
        select(DramaAsset)
        .join(DramaProject, DramaAsset.project_id == DramaProject.id)
        .order_by(DramaAsset.updated_at.desc())
    )
    if project_id is not None:
        await get_owned_drama_project(db, project_id, user)
        q = q.where(DramaAsset.project_id == project_id)
    elif isinstance(scope_team_id, int):
        q = q.where(DramaProject.team_id == scope_team_id)
    else:
        q = q.where(DramaProject.user_id == user.id, DramaProject.team_id.is_(None))
    rows = list((await db.execute(q)).scalars().all())
    if library_only:
        canvas_only_types = {"video", "audio", "text"}
        rows = [
            asset
            for asset in rows
            if (asset.type or "").lower() not in canvas_only_types
            and (asset.asset_type or "").lower() != "video"
        ]
    return [DramaAssetOut.model_validate(a) for a in rows]


@router.post("/assets", response_model=DramaAssetOut)
async def create_asset(
    body: DramaAssetCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaAssetOut:
    await get_owned_drama_project(db, body.project_id, user)
    # 同类型同名已存在则直接返回，避免手动/并发再造重复卡
    want_name = _normalize_asset_name(body.name or "")
    if want_name:
        want_key = _asset_dedupe_key(body.type or "", want_name)
        existing_rows = list(
            (
                await db.execute(
                    select(DramaAsset).where(DramaAsset.project_id == body.project_id)
                )
            )
            .scalars()
            .all()
        )
        for row in existing_rows:
            key = _asset_dedupe_key(row.type or "", _normalize_asset_name(row.name or ""))
            if key == want_key:
                return DramaAssetOut.model_validate(row)
    asset = DramaAsset(
        project_id=body.project_id,
        type=body.type,
        asset_type=body.asset_type,
        name=body.name,
        cover=body.cover,
        url=body.url,
        params=body.params,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return DramaAssetOut.model_validate(asset)


def _merge_asset_params(prev: dict | None, incoming: dict | None) -> dict:
    """合并资产 params：客户端全量写回时保留 image_versions / 进行中的 generation。"""
    base = dict(prev or {}) if isinstance(prev, dict) else {}
    patch = dict(incoming or {}) if isinstance(incoming, dict) else {}
    out = {**base, **patch}

    prev_gen = base.get("generation") if isinstance(base.get("generation"), dict) else None
    prev_status = str((prev_gen or {}).get("status") or "").lower()
    if prev_status in {"queued", "running", "generating"}:
        # 生图进行中禁止被陈旧客户端状态覆盖
        out["generation"] = prev_gen

    prev_vers = base.get("image_versions")
    inc_vers = patch.get("image_versions") if "image_versions" in patch else None
    if isinstance(prev_vers, list) and prev_vers:
        if not isinstance(inc_vers, list):
            out["image_versions"] = prev_vers
        else:
            by_id: dict[str, dict] = {}
            order: list[str] = []
            for row in [*prev_vers, *inc_vers]:
                if not isinstance(row, dict):
                    continue
                vid = str(row.get("id") or "").strip()
                if not vid:
                    continue
                if vid not in by_id:
                    order.append(vid)
                by_id[vid] = row
            out["image_versions"] = [by_id[vid] for vid in order][:8]
    return out


@router.patch("/assets/{asset_id}", response_model=DramaAssetOut)
async def update_asset(
    asset_id: int,
    body: DramaAssetUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaAssetOut:
    from app.models_drama import DramaProject

    asset = await get_owned_asset(db, asset_id, user)
    for field in ("type", "asset_type", "name", "cover", "url", "params"):
        val = getattr(body, field)
        if val is None:
            continue
        if field == "params" and isinstance(val, dict):
            asset.params = _merge_asset_params(
                asset.params if isinstance(asset.params, dict) else {},
                val,
            )
        else:
            setattr(asset, field, val)
    await db.commit()
    await db.refresh(asset)
    return DramaAssetOut.model_validate(asset)


@router.post("/assets/{asset_id}/upload", response_model=DramaAssetOut)
async def upload_asset_media(
    asset_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaAssetOut:
    """上传图片或音频到 OSS（不落本地）；音频资产不写 cover。"""
    from app.models_drama import DramaProject
    from app.services import oss as oss_svc

    if not oss_svc.oss_enabled():
        raise HTTPException(status_code=503, detail="OSS 未启用，无法上传资产媒体")

    asset = await get_owned_asset(db, asset_id, user)

    gen = (asset.params or {}).get("generation") if isinstance(asset.params, dict) else None
    status = str((gen or {}).get("status") or "").lower() if isinstance(gen, dict) else ""
    if status in {"queued", "running", "generating"}:
        raise HTTPException(status_code=409, detail="形象生成中，请稍后再更换图片")

    content_type = (file.content_type or "").lower()
    image_allowed = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    audio_allowed = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
    }
    ext = image_allowed.get(content_type) or audio_allowed.get(content_type)
    is_audio = content_type in audio_allowed
    if not ext:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            ext = ".jpg" if suffix == ".jpeg" else suffix
            content_type = {
                ".jpg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(ext, "application/octet-stream")
            is_audio = False
        elif suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm"}:
            ext = suffix
            content_type = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".aac": "audio/aac",
                ".ogg": "audio/ogg",
                ".webm": "audio/webm",
            }.get(ext, "application/octet-stream")
            is_audio = True
        else:
            raise HTTPException(status_code=400, detail="仅支持图片（JPG/PNG/WebP/GIF）或音频（MP3/WAV/M4A 等）")

    # 直接检查上传临时文件大小，避免先把整文件读入内存。
    upload_fp = file.file
    try:
        upload_fp.seek(0, 2)
        size = upload_fp.tell()
        upload_fp.seek(0)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取上传文件：{exc}") from exc
    if size <= 0:
        raise HTTPException(status_code=400, detail="空文件")
    max_bytes = 30 * 1024 * 1024 if is_audio else 20 * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status_code=400,
            detail="音频不能超过 30MB" if is_audio else "文件不能超过 20MB",
        )

    object_key = (
        f"{oss_svc.folder_prefix()}/generated/p{asset.project_id}/"
        f"asset_{asset.id}_{uuid.uuid4().hex[:10]}{ext}"
    )
    try:
        # OSS SDK 为同步阻塞 IO，放到线程池里避免阻塞事件循环。
        url = await run_in_threadpool(
            oss_svc.upload_fileobj,
            upload_fp,
            object_key,
            content_type=content_type or "application/octet-stream",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OSS 上传失败：{exc}") from exc

    from app.services.drama.generation import archive_asset_image_version

    if not is_audio:
        archive_asset_image_version(asset, source="upload")
    asset.url = url
    if is_audio:
        # 音频资产不把 mp3 写进 cover，避免前端当图片加载
        if not asset.asset_type or asset.asset_type in {"none", "image"}:
            asset.asset_type = "audio"
        if (asset.type or "").lower() in {"", "none", "image", "prop"}:
            asset.type = "audio"
    else:
        asset.cover = url
        if not asset.asset_type or asset.asset_type == "none":
            asset.asset_type = "image"
    params = dict(asset.params or {})
    gen = params.get("generation")
    if isinstance(gen, dict):
        params["generation"] = {**gen, "status": "done", "source": "upload"}
    else:
        params["generation"] = {"status": "done", "source": "upload"}
    asset.params = params
    await db.commit()
    await db.refresh(asset)
    return DramaAssetOut.model_validate(asset)


@router.post("/assets/{asset_id}/activate_image_version", response_model=DramaAssetOut)
async def activate_image_version(
    asset_id: int,
    body: DramaActivateImageVersionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DramaAssetOut:
    """将资产形象历史版本设为当前。"""
    from app.services.drama.generation import activate_asset_image_version

    asset = await get_owned_asset(db, asset_id, user)

    gen = (asset.params or {}).get("generation") if isinstance(asset.params, dict) else None
    status = str((gen or {}).get("status") or "").lower() if isinstance(gen, dict) else ""
    if status in {"queued", "running", "generating"}:
        raise HTTPException(status_code=409, detail="形象生成中，无法切换历史版本")

    try:
        activate_asset_image_version(asset, body.version_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(asset)
    return DramaAssetOut.model_validate(asset)


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    from app.models_drama import DramaProject

    asset = await get_owned_asset(db, asset_id, user)
    await db.delete(asset)
    await db.commit()
    return {"ok": True}


@router.post("/assets/seed_from_script", response_model=SeedAssetsFromScriptOut)
async def seed_assets(
    project_id: int,
    refresh_prompts: bool = False,
    reextract_props: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SeedAssetsFromScriptOut:
    project = await get_owned_drama_project(db, project_id, user, with_script=True)
    heavy = bool(refresh_prompts or reextract_props)

    # 行锁：防止空库并发 seed 插入同名角色
    locked = (
        await db.execute(
            select(DramaProject).where(DramaProject.id == project.id).with_for_update()
        )
    ).scalar_one()
    params = dict(locked.params or {}) if isinstance(locked.params, dict) else {}
    if str(params.get("assets_seed_status") or "") == "generating":
        existing = list(
            (await db.execute(select(DramaAsset).where(DramaAsset.project_id == locked.id)))
            .scalars()
            .all()
        )
        return SeedAssetsFromScriptOut(
            assets=[DramaAssetOut.model_validate(a) for a in existing],
            status="generating",
            message="资产抽取进行中，请稍候刷新",
        )

    if heavy:
        params["assets_seed_status"] = "generating"
        params["assets_seed_generating_at"] = datetime.now(timezone.utc).isoformat()
        params.pop("assets_seed_error", None)
        locked.params = params
        try:
            await dispatch_seed_assets_job(
                db,
                user,
                project_id,
                refresh_prompts=refresh_prompts,
                reextract_props=reextract_props,
            )
        except ValueError as exc:
            await db.rollback()
            raise http_exception_for_value_error(exc) from exc
        existing = list(
            (await db.execute(select(DramaAsset).where(DramaAsset.project_id == locked.id)))
            .scalars()
            .all()
        )
        return SeedAssetsFromScriptOut(
            assets=[DramaAssetOut.model_validate(a) for a in existing],
            status="generating",
            message="资产抽取任务已提交，请稍候刷新",
        )

    params["assets_seed_status"] = "generating"
    params["assets_seed_generating_at"] = datetime.now(timezone.utc).isoformat()
    params.pop("assets_seed_error", None)
    locked.params = params
    await db.commit()
    try:
        async def _do_sync_seed():
            project_inner = await get_owned_drama_project(db, project_id, user, with_script=True)
            seed_result = await seed_assets_from_script(
                db,
                project_inner,
                refresh_prompts=refresh_prompts,
                reextract_props=reextract_props,
            )
            await record_seed_assets_llm_usage(db, user, project_id, seed_result)
            return seed_result

        _task, result = await run_billed_ephemeral(
            db,
            user,
            domain="drama",
            task_type="seed_assets",
            executor=_do_sync_seed,
            drama_project_id=project_id,
            payload={"sync": True, "refresh_prompts": refresh_prompts, "reextract_props": reextract_props},
            commit=False,
        )
        project = await get_owned_drama_project(db, project_id, user, with_script=True)
        params = dict(project.params or {}) if isinstance(project.params, dict) else {}
        params["assets_seed_status"] = "done"
        params.pop("assets_seed_error", None)
        params.pop("assets_seed_generating_at", None)
        project.params = params
        await db.commit()
    except ValueError as exc:
        project = await get_owned_drama_project(db, project_id, user, with_script=True)
        params = dict(project.params or {}) if isinstance(project.params, dict) else {}
        params["assets_seed_status"] = "failed"
        params["assets_seed_error"] = str(exc)[:500]
        params.pop("assets_seed_generating_at", None)
        project.params = params
        await db.commit()
        raise http_exception_for_value_error(exc) from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        project = await get_owned_drama_project(db, project_id, user, with_script=True)
        params = dict(project.params or {}) if isinstance(project.params, dict) else {}
        params["assets_seed_status"] = "failed"
        params["assets_seed_error"] = str(exc)[:500]
        params.pop("assets_seed_generating_at", None)
        project.params = params
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except Exception:
        project = await get_owned_drama_project(db, project_id, user, with_script=True)
        params = dict(project.params or {}) if isinstance(project.params, dict) else {}
        params["assets_seed_status"] = "failed"
        params["assets_seed_error"] = "资产抽取失败"
        params.pop("assets_seed_generating_at", None)
        project.params = params
        await db.commit()
        raise
    return SeedAssetsFromScriptOut(
        assets=[DramaAssetOut.model_validate(a) for a in result.assets],
        created_count=result.created_count,
        prompts_refreshed=result.prompts_refreshed,
        props_updated=result.props_updated,
        llm_errors=result.llm_errors,
        status="done",
    )
