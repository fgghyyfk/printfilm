"""Build all media operations before admission, including composite-tool helper images."""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Project
from app.services.logical_model_router import resolve_logical_model_id, resolve_upstream_model
from .prices import active_book, expected_price_version, resolve_strategy
from .runtime import pin_route
from . import MediaValidationError, get_strategy

REFERENCE_FIELDS = ("reference_images", "reference_audios", "reference_videos", "ref_urls",
                    "style_ref_urls", "image_url", "first_frame", "last_frame", "content")


def reference_params(payload: dict) -> dict:
    return {key: payload[key] for key in REFERENCE_FIELDS if key in payload}


async def canvas_video_reference_content(db, task, payload: dict) -> list[dict]:
    """Build the same reference slots as the worker without publishing or generating media."""
    from app.models_drama import DramaProject
    from app.services.drama.asset_video import load_reference_assets, merge_reference_asset_ids
    from app.services.drama.build_seedance_generate_body import build_seedance_content_items, drama_asset_to_payload
    from app.services.drama.image_styles import resolve_image_style_board_url

    project_id = task.drama_project_id or payload.get("project_id")
    project = await db.get(DramaProject, project_id)
    if not project:
        raise MediaValidationError("project_id", "项目不存在")
    prompt = str(payload.get("prompt") or "")
    ids = merge_reference_asset_ids(prompt, payload.get("reference_asset_ids"), self_asset_id=task.asset_id)
    assets = await load_reference_assets(db, project.id, ids)
    style_id = payload.get("image_style_id") or (project.params or {}).get("image_style_id")
    return build_seedance_content_items(prompt, [drama_asset_to_payload(asset) for asset in assets],
                                       style_id, style_board_url=resolve_image_style_board_url(style_id))


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
        if not model:
            resolve_upstream_model(capability, None)
            model = resolve_logical_model_id(capability, None) or (
                settings.model_image if capability == "image" else settings.model_video
            )
        strategy = resolve_strategy(model)
        if strategy.capability != capability:
            raise MediaValidationError("model", "模型类型与生成任务不匹配")
        normalized = strategy.normalize_and_validate(params or {})
        items.append({"operation_id": f"{capability}:{target if target is not None else len(items)}",
                      "capability": capability, "model_id": strategy.model_id, "strategy_id": strategy.model_id,
                      "strategy_version": strategy.version, "params": normalized,
                      "reference_limits": strategy.describe()["reference_limits"],
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
                    model=project.image_model, target=shot.id, shot_no=shot.shot_no)
            if phase == "videos" and (kind != "project_pipeline" or not shot_video_ready(shot)):
                from app.services.seedance_resolutions import project_video_resolution
                resolution = project_video_resolution(project, settings)
                add("video", {"duration": int(shot.duration or 5), "resolution": resolution,
                              "aspect_ratio": _project_output_ratio(project) or "9:16"},
                    model=project.video_model, target=shot.id, shot_no=shot.shot_no)
        return items
    if kind in {"asset_image", "tool_image"}:
        model = p.get("model_id") or p.get("model") or generation.get("model")
        params = {"aspect_ratio": p.get("aspect_ratio") or p.get("ratio") or generation.get("aspect_ratio") or "1:1",
                  "resolution": p.get("resolution") or p.get("size") or generation.get("resolution") or "2K",
                  "count": p.get("count", 1), **reference_params(p)}
        if kind == "asset_image":
            from app.models_drama import DramaProject
            from app.services.drama.image_styles import resolve_image_style_board_url
            style_id = p.get("image_style_id")
            project_id = task.drama_project_id or p.get("project_id")
            if not style_id and db is not None and project_id:
                project = await db.get(DramaProject, project_id)
                style_id = (project.params or {}).get("image_style_id") if project else None
            board = resolve_image_style_board_url(style_id)
            if board:
                params["style_ref_urls"] = [*(params.get("style_ref_urls") or []), board]
        if kind == "asset_image" and not p.get("aspect_ratio"):
            params["aspect_ratio"] = "3:4" if p.get("kind", "character") == "character" else "16:9"
        add("image", params, model=model, target=task.asset_id or task.shot_id)
    if kind in {"asset_video", "fragment_video", "tool_video"}:
        body = dict(mapping(prepared.get("seedance_body"), "prepared.seedance_body"))
        if kind == "asset_video":
            # Canvas workers load linked assets themselves; client-prepared content cannot hide them.
            body = {}
        model = p.get("model_id") or p.get("model") or body.get("model") or prepared.get("model_id")
        duration = first(p.get("duration_sec"), p.get("duration"), body.get("duration"), prepared.get("duration"))
        resolution = p.get("resolution") or body.get("resolution") or prepared.get("resolution")
        ratio = p.get("aspect_ratio") or p.get("ratio") or body.get("ratio") or prepared.get("ratio")
        helper_needed = kind == "tool_video" and p.get("tool_id") == "t2v" and not p.get("video_mode")
        references = reference_params(p)
        if kind == "asset_video":
            references["content"] = await canvas_video_reference_content(db, task, p)
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
                    from app.services.drama.generation import build_fragment_ref_payloads, find_previous_episode_fragment, project_link_last_frame_enabled, read_fragment_last_frame_url
                    from app.services.drama.build_seedance_generate_body import build_seedance_content_items, build_seedance_reference_catalog
                    from app.services.drama.image_styles import resolve_image_style_board_url
                    mentioned = [int(value) for value in re.findall(r"@asset:(\d+)", fragment.content or "")]
                    linked = select(DramaFragmentAssetRef.asset_id).where(DramaFragmentAssetRef.fragment_id == fragment.id)
                    assets = list((await db.execute(select(DramaAsset).where(DramaAsset.project_id == project.id,
                        or_(DramaAsset.id.in_(linked), DramaAsset.id.in_(mentioned))))).scalars().all())
                    payloads = build_fragment_ref_payloads(project, assets)
                    catalog = build_seedance_reference_catalog(payloads, script=fragment.content or "")
                    helper_needed = not (catalog.images or catalog.audios or catalog.videos)
                    previous = await find_previous_episode_fragment(db, fragment) if project_link_last_frame_enabled(project) else None
                    continuity = (read_fragment_last_frame_url(previous) or "pending://previous-frame") if previous and previous.video else None
                    style_id = (project.params or {}).get("image_style_id")
                    references["content"] = build_seedance_content_items(fragment.content or "", payloads, style_id,
                        continuity, style_board_url=resolve_image_style_board_url(style_id))
                else:
                    helper_needed = True
        references.update(reference_params(body))
        if kind == "fragment_video" and prepared.get("image_url"):
            references["image_url"] = prepared["image_url"]
        content = references.get("content")
        content_images = isinstance(content, list) and any(isinstance(item, dict) and item.get("type") == "image_url" for item in content)
        if helper_needed and not content_images and not any(references.get(key) for key in ("image_url", "first_frame", "reference_images")):
            references["image_url"] = "pending://generated-first-frame"
        add("video", {"duration": duration if duration is not None else (5 if task.domain == "studio" else 8),
                      "resolution": resolution or ("480p" if task.domain == "studio" else "720p"),
                      "aspect_ratio": ratio or "9:16",
                      **({"generate_audio": p["generate_audio"]} if "generate_audio" in p else {}),
                      **references},
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
            item.update(get_strategy(item["strategy_id"]).with_reference_limits(item["reference_limits"]).quote(item["params"], book["prices"]))
    task.billing_snapshot = {"mode": "fixed_media" if book else "legacy", "version": 1,
                             "price_version": book["version"] if book else None, "items": items}
