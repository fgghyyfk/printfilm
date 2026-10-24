"""Build all media operations before admission, including composite-tool helper images."""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Project
from .prices import active_book, expected_price_version, resolve_strategy
from .runtime import pin_route
from . import MediaValidationError, get_strategy


async def plan_items(db, task) -> list[dict]:
    settings = get_settings()
    p = task.payload or {}
    def mapping(value, field):
        if value is not None and not isinstance(value, dict):
            raise MediaValidationError(field, "必须是对象")
        return value or {}
    def first(*values):
        return next((value for value in values if value is not None and value != ""), None)
    prepared = mapping(p.get("prepared"), "prepared")
    canvas = mapping(p.get("canvas"), "canvas")
    generation = mapping(p.get("generation") or canvas.get("generation"), "generation")
    items = []

    def add(capability, params=None, *, model=None, target=None, shot_no=None, optional=False):
        if model is not None and not isinstance(model, str):
            raise MediaValidationError("model", "必须是模型 ID 字符串")
        model = model or (settings.model_image if capability == "image" else settings.model_video)
        strategy = resolve_strategy(model)
        normalized = strategy.normalize_and_validate(params or {})
        items.append({"operation_id": f"{capability}:{target if target is not None else len(items)}",
                      "capability": capability, "model_id": strategy.model_id, "strategy_id": strategy.model_id,
                      "strategy_version": strategy.version, "params": normalized,
                      "target_id": target, "shot_no": shot_no, "optional": optional,
                      "route": pin_route(capability, model)})

    kind = task.task_type
    if task.domain == "kepu" and kind in {"project_pipeline", "shot_regen_image", "shot_regen_video"}:
        project = (await db.execute(select(Project).where(Project.id == task.project_id).options(selectinload(Project.shots), selectinload(Project.template)))).scalar_one_or_none() if task.project_id else None
        if not project:
            return []
        from app.services.kepu_stages import normalize_kepu_pipeline_phase, shot_image_ready, shot_video_ready
        from app.services.pipeline import _image_size_for, _project_output_ratio
        phase = normalize_kepu_pipeline_phase(str(p.get("phase") or "script"), project)
        shots = project.shots
        if kind != "project_pipeline":
            shots = [x for x in shots if x.id == task.shot_id]
            phase = "assets" if kind == "shot_regen_image" else "videos"
        for shot in shots:
            if phase == "assets" and (kind != "project_pipeline" or not shot_image_ready(shot)):
                add("image", {"size": _image_size_for(project) or settings.ark_image_size,
                              "aspect_ratio": _project_output_ratio(project) or "1:1"},
                    model=project.image_model or settings.model_image, target=shot.id, shot_no=shot.shot_no)
            if phase == "videos" and (kind != "project_pipeline" or not shot_video_ready(shot)):
                from app.services.seedance_resolutions import project_video_resolution
                resolution = project_video_resolution(project, settings)
                add("video", {"duration": int(shot.duration or 5), "resolution": resolution,
                              "aspect_ratio": _project_output_ratio(project) or "9:16"},
                    model=project.video_model or settings.model_video, target=shot.id, shot_no=shot.shot_no)
        return items
    if kind in {"asset_image", "v1_image", "tool_image"}:
        model = p.get("model_id") or p.get("model") or generation.get("model")
        params = {"aspect_ratio": p.get("aspect_ratio") or p.get("ratio") or generation.get("aspect_ratio") or "1:1",
                  "resolution": p.get("resolution") or p.get("size") or generation.get("resolution") or "2K",
                  "count": p.get("count", 1)}
        if kind == "asset_image" and not p.get("aspect_ratio"):
            params["aspect_ratio"] = "3:4" if p.get("kind", "character") == "character" else "16:9"
        add("image", params, model=model, target=task.asset_id or task.shot_id)
    if kind in {"asset_video", "fragment_video", "v1_video", "v1_seedance", "tool_video"}:
        body = mapping(prepared.get("seedance_body"), "prepared.seedance_body")
        model = p.get("model_id") or p.get("model") or body.get("model") or prepared.get("model_id")
        duration = first(p.get("duration_sec"), p.get("duration"), body.get("duration"), prepared.get("duration"))
        resolution = p.get("resolution") or body.get("resolution") or prepared.get("resolution")
        ratio = p.get("aspect_ratio") or p.get("ratio") or body.get("ratio") or prepared.get("ratio")
        helper_needed = kind == "tool_video" and p.get("tool_id") == "t2v"
        if kind == "fragment_video":
            from app.models_drama import DramaEpisodeFragment, DramaEpisode, DramaProject, DramaAsset, DramaFragmentAssetRef
            from app.services.drama.fragment_content_duration import resolve_seedance_duration_from_content
            from app.services.drama.output_settings import resolve_episode_video_output
            fragment_id = task.fragment_id or next(iter(p.get("fragment_ids") or []), None)
            fragment = await db.get(DramaEpisodeFragment, fragment_id) if fragment_id else None
            if fragment:
                if duration is None:
                    duration = resolve_seedance_duration_from_content(fragment.content or "", fallback=int(fragment.duration_sec or 8))
                episode = await db.get(DramaEpisode, fragment.episode_id)
                project = await db.get(DramaProject, task.drama_project_id) if task.drama_project_id else None
                ep_ratio, ep_res = resolve_episode_video_output(episode.params if episode else None, project.params if project else None)
                ratio, resolution = ratio or ep_ratio, resolution or ep_res
                if prepared.get("submit_mode") == "seedance_body" or prepared.get("image_url"):
                    helper_needed = False
                elif project:
                    from sqlalchemy import or_
                    from app.services.drama.generation import build_fragment_ref_payloads
                    from app.services.drama.build_seedance_generate_body import build_seedance_reference_catalog
                    mentioned = [int(value) for value in re.findall(r"@asset:(\d+)", fragment.content or "")]
                    linked = select(DramaFragmentAssetRef.asset_id).where(DramaFragmentAssetRef.fragment_id == fragment.id)
                    assets = list((await db.execute(select(DramaAsset).where(DramaAsset.project_id == project.id,
                        or_(DramaAsset.id.in_(linked), DramaAsset.id.in_(mentioned))))).scalars().all())
                    references = build_seedance_reference_catalog(build_fragment_ref_payloads(project, assets))
                    helper_needed = not (references.images or references.audios)
                else:
                    helper_needed = True
        add("video", {"duration": duration if duration is not None else (5 if task.domain in {"studio", "api"} else 8),
                      "resolution": resolution or ("480p" if task.domain in {"studio", "api"} else "720p"),
                      "aspect_ratio": ratio or "9:16",
                      **({"generate_audio": p["generate_audio"]} if "generate_audio" in p else {}),
                      **({"content": body["content"]} if "content" in body else {})},
            model=model, target=task.fragment_id or task.asset_id or task.shot_id)
        if helper_needed:
            add("image", {"resolution": "2K", "aspect_ratio": ratio or "9:16"}, optional=True)
    return items


async def prepare_task_media(db, task) -> None:
    if task.billing_snapshot is not None:
        return
    items = await plan_items(db, task)
    if not items:
        return
    book = await active_book(db)
    expected = expected_price_version.get()
    if expected is None:
        expected = (task.payload or {}).get("price_version")
    if expected is not None and expected != (book["version"] if book else 0):
        raise HTTPException(409, detail="价格已更新，请刷新报价后重新提交")
    for item in items:
        if book:
            item.update(get_strategy(item["strategy_id"]).quote(item["params"], book["prices"]))
    task.billing_snapshot = {"mode": "fixed_media" if book else "legacy", "version": 1,
                             "price_version": book["version"] if book else None, "items": items}
