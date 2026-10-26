"""Combine authorized project assets and delivered standalone tool results."""
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import ToolRun
from app.models_drama import DramaAsset, DramaProject
from app.schemas_media_library import MediaLibraryItem, MediaLibraryMeta, MediaLibraryPage
from app.services.drama.workflow import resolve_drama_workflow


def media_kind(primary: str, secondary: str, url: str = "") -> str:
    primary, secondary = primary.lower(), secondary.lower()
    if "video" in (primary, secondary):
        return "video"
    if primary in {"voice", "audio"} or secondary in {"voice", "audio"}:
        return "audio"
    suffix = url.split("?", 1)[0].split("#", 1)[0].lower()
    if suffix.endswith((".mp4", ".webm", ".mov", ".m4v")):
        return "video"
    if suffix.endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")):
        return "audio"
    return "image"


def delivered_urls(value) -> list[tuple[int, str]]:
    """Only formal output URLs count; previews and local upload paths do not."""
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for index, url in enumerate(value):
        if not isinstance(url, str):
            continue
        url = url.strip()
        if url and url not in seen and (url.startswith(("https://", "http://")) or url.startswith("/static/")):
            result.append((index, url))
            seen.add(url)
    return result


def tool_items(run: ToolRun) -> list[MediaLibraryItem]:
    if run.kind not in {"image", "video", "audio"}:
        return []
    params = run.params if isinstance(run.params, dict) else {}
    # Do not return internal task credentials, local file paths or upstream request bodies.
    options = {key: params[key] for key in ("resolution", "aspect_ratio", "size", "generate_audio", "seed")
               if isinstance(params.get(key), (str, int, float, bool))}
    if "ratio" in params and isinstance(params["ratio"], str):
        options["aspect_ratio"] = params["ratio"]
    if run.kind == "video":
        raw = params.get("duration_sec", params.get("duration", ""))
        try:
            duration = float(str(raw).removesuffix("s"))
            if 0 < duration < 36000:
                options["duration_sec"] = duration
        except (TypeError, ValueError):
            pass
    if run.kind == "audio" and isinstance(params.get("voice"), str):
        options["speaker"] = params["voice"]
    labels = {"image": "图片", "video": "视频", "audio": "音频"}
    rows = []
    for index, url in delivered_urls(run.urls):
        poster = run.preview_url if run.kind == "video" and run.preview_url != url else None
        rows.append(MediaLibraryItem(
            key=f"tool:{run.id}:{index}", id=run.id, source_kind="tool", source_id=run.id,
            type=run.kind, asset_type=run.kind, media_kind=run.kind,
            name=f"{labels[run.kind]}生成 #{run.id} · {index + 1}", url=url,
            cover=url if run.kind == "image" else poster,
            updated_at=run.updated_at or run.created_at,
            params={"media_metadata": {"source": "generated", "prompt": run.prompt,
                "model_id": params.get("model_id") if isinstance(params.get("model_id"), str) else None,
                "parameters": options, "created_at": (run.updated_at or run.created_at).isoformat()}},
        ))
    return rows


async def library_page(db, user_id: int, team_id: int | None, *, kind="all", project_id=None,
                       unassigned=False, q="", page=1, page_size=12) -> MediaLibraryPage:
    scope = (DramaProject.team_id == team_id) if team_id is not None else (
        (DramaProject.user_id == user_id) & DramaProject.team_id.is_(None))
    # Explicit project filters must belong to the selected workspace too.
    if project_id is not None and await db.scalar(select(DramaProject.id).where(scope, DramaProject.id == project_id)) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    statement = select(DramaAsset, DramaProject).join(DramaProject, DramaProject.id == DramaAsset.project_id).where(scope)
    if project_id is not None:
        statement = statement.where(DramaProject.id == project_id)
    statement = statement.options(selectinload(DramaProject.script))
    items = []
    if not unassigned:
        for asset, project in (await db.execute(statement)).all():
            primary = (asset.type or "").lower()
            if primary not in {"character", "scene", "prop", "voice", "image", "video", "audio"}:
                continue
            media = media_kind(primary, asset.asset_type or "", asset.url or "")
            suffix = "/canvas" if resolve_drama_workflow(project) == "canvas" else ""
            items.append(MediaLibraryItem(
                key=f"drama:{asset.id}", id=asset.id, source_kind="drama", source_id=asset.id,
                type=primary, asset_type=asset.asset_type, media_kind=media, name=asset.name,
                cover=asset.cover, url=asset.url, params=asset.params, updated_at=asset.updated_at,
                project_id=project.id, project_title=project.title,
                project_url=f"/drama/projects/{project.id}{suffix}",
            ))
    if team_id is None and project_id is None:
        runs = (await db.execute(select(ToolRun).where(ToolRun.user_id == user_id,
            ToolRun.kind.in_(["image", "video", "audio"]), ToolRun.urls.is_not(None)))).scalars()
        for run in runs:
            # Partial delivery survives failed/cancelled task status.
            items.extend(tool_items(run))
    total_all = len(items)
    search = q.strip().casefold()
    items = [item for item in items if (
        kind == "all" or (item.media_kind == kind if kind in {"image", "video", "audio"} else item.type == kind)
    ) and (not search or search in " ".join((item.name or "", item.project_title or "", item.type,
        str(item.project_id or ""))).casefold())]
    items.sort(key=lambda item: ((item.updated_at or datetime(1970, 1, 1, tzinfo=UTC)).timestamp(), item.key), reverse=True)
    total = len(items)
    page = min(page, max(1, (total + page_size - 1) // page_size))
    return MediaLibraryPage(items=items[(page - 1) * page_size:page * page_size],
        meta=MediaLibraryMeta(total=total, total_all=total_all, page=page, page_size=page_size))
