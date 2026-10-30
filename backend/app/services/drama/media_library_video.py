"""Read delivered video entities and immutable task outputs in their real workspace."""
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.models import Shot, ToolRun
from app.models_drama import DramaEpisode, DramaEpisodeFragment
from app.models_tasks import TaskRun
from app.schemas_media_library import MediaLibraryItem
from app.services.tasks.generation_history import (
    _kind_expression, _safe_url, _submitted_evidence, _tool_link, project_generation,
)
from app.services.tasks.generation_metadata import public_generation_params
from app.services.tasks.generation_references import public_references


def project_fields(project, project_kind: str) -> dict:
    if project_kind == "short_video":
        url = f"/studio/{project.id}/editor"
    else:
        from app.services.drama.workflow import resolve_drama_workflow
        suffix = "/canvas" if resolve_drama_workflow(project) == "canvas" else ""
        url = f"/drama/projects/{project.id}{suffix}"
    return dict(project_id=project.id, project_kind=project_kind, project_title=project.title, project_url=url)


def video_item(*, key, source_kind, source_id, name, url, cover=None, params=None, references=None,
               updated_at=None, lineage=None, **project):
    url = _safe_url(url)
    if not url:
        return None
    item = MediaLibraryItem(key=key, id=source_id, source_kind=source_kind, source_id=source_id,
        type="video", asset_type="video", media_kind="video", name=name, url=url,
        cover=_safe_url(cover), params=params, references=references or [], updated_at=updated_at, **project)
    item._lineage = lineage
    return item


def metadata(prompt, parameters, created_at, *, request=False, references=None):
    safe = public_generation_params(parameters)
    for key in ("media_duration_sec", "media_width", "media_height"):
        value = parameters.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1_000_000:
            safe[key] = value
    if request:
        raw = safe.get("duration_sec", safe.get("duration"))
        try:
            duration = float(str(raw).strip().removesuffix("s"))
            if 0 < duration < 36000:
                safe["requested_duration_sec"] = duration
        except (TypeError, ValueError):
            pass
        for key in ("duration", "duration_sec", "seconds"):
            safe.pop(key, None)
    return {"media_metadata": {"source": "generated", "prompt": prompt,
        "model_id": safe.get("model_id"), "parameters": safe,
        "created_at": created_at.isoformat() if created_at else None,
        "references": public_references(references),
        **({"parameter_source": "request"} if request else {})}}


def task_project(task, dramas, shorts, user_id, team_id):
    """A stale task foreign key or a removed team cannot turn into personal media."""
    if task.team_id != team_id:
        return None
    if team_id is None and task.requested_by != user_id:
        return None
    if task.drama_project_id is not None:
        project = dramas.get(task.drama_project_id)
        return project_fields(project, "drama") if project is not None else None
    if task.project_id is not None:
        project = shorts.get(task.project_id)
        return project_fields(project, "short_video") if project is not None else None
    return {} if team_id is None else None


async def video_items(db, user_id, team_id, dramas, shorts, *, include_unassigned=True):
    items = []
    if dramas:
        rows = (await db.execute(select(DramaEpisodeFragment, DramaEpisode)
            .join(DramaEpisode, DramaEpisode.id == DramaEpisodeFragment.episode_id)
            .where(DramaEpisode.project_id.in_(dramas)))).all()
        for fragment, episode in rows:
            fields = project_fields(dramas[episode.project_id], "drama")
            name = f"{episode.name} · 分镜 {fragment.sort_order + 1}"
            params = fragment.params if isinstance(fragment.params, dict) else {}
            row = video_item(key=f"drama_fragment:{fragment.id}", source_kind="drama_fragment", source_id=fragment.id,
                name=name, url=fragment.video, cover=fragment.cover,
                updated_at=fragment.updated_at,
                lineage=("fragment", fragment.id), **fields)
            if row:
                items.append(row)
            # Archive entries are formal prior takes, never input/preview references.
            versions = params.get("video_versions")
            for index, version in enumerate(versions if isinstance(versions, list) else []):
                if not isinstance(version, dict):
                    continue
                try:
                    stamp = datetime.fromisoformat(str(version.get("createdAt") or "").replace("Z", "+00:00"))
                except ValueError:
                    stamp = fragment.updated_at
                row = video_item(key=f"drama_fragment:{fragment.id}:version:{index}", source_kind="drama_fragment",
                    source_id=fragment.id, name=f"{name} · 历史版本 {index + 1}", url=version.get("video"),
                    cover=version.get("cover"), updated_at=stamp, lineage=("fragment", fragment.id), **fields)
                if row:
                    items.append(row)
    if shorts:
        for project in shorts.values():
            row = video_item(key=f"short_video:project:{project.id}", source_kind="short_video", source_id=project.id,
                name=f"{project.title} · 成片", url=project.final_video_url, cover=project.cover_url,
                updated_at=project.updated_at, lineage=("project", project.id), **project_fields(project, "short_video"))
            if row:
                items.append(row)
        shots = (await db.scalars(select(Shot).where(Shot.project_id.in_(shorts), Shot.video_url.is_not(None)))).all()
        for shot in shots:
            project = shorts[shot.project_id]
            row = video_item(key=f"short_video:shot:{shot.id}", source_kind="short_video", source_id=shot.id,
                name=f"{project.title} · 镜头 {shot.shot_no}", url=shot.video_url, cover=shot.image_url,
                updated_at=project.updated_at,
                lineage=("shot", shot.id), **project_fields(project, "short_video"))
            if row:
                items.append(row)
    scopes = []
    if dramas:
        scopes.append(TaskRun.drama_project_id.in_(dramas))
    if shorts:
        scopes.append(and_(TaskRun.drama_project_id.is_(None), TaskRun.project_id.in_(shorts)))
    if team_id is None and include_unassigned:
        scopes.append(and_(TaskRun.drama_project_id.is_(None), TaskRun.project_id.is_(None)))
    if not scopes:
        return items, set()
    scope = [or_(*scopes), TaskRun.team_id == team_id if team_id is not None else TaskRun.team_id.is_(None)]
    if team_id is None:
        scope.append(TaskRun.requested_by == user_id)
    tasks = (await db.scalars(select(TaskRun).where(*scope, _kind_expression() == "video",
        TaskRun.status.in_(("succeeded", "failed", "cancelled"))).options(selectinload(TaskRun.steps))
        .order_by(TaskRun.id.desc()))).all()
    if not tasks:
        return items, set()
    ids = {task.id for task in tasks}
    tools = {}
    for run, task_id in (await db.execute(select(ToolRun, TaskRun.id).join(TaskRun, _tool_link())
            .where(TaskRun.id.in_(ids), ToolRun.kind == "video"))).all():
        tools.setdefault(task_id, []).append(run)
    submitted = await _submitted_evidence(db, tasks)
    for task in tasks:
        fields = task_project(task, dramas, shorts, user_id, team_id)
        if fields is None:
            continue
        linked = tools.get(task.id, [])
        record = project_generation(task, viewer_id=user_id, linked_tool=linked[0] if linked else None,
            submitted_params=submitted.get(task.id, {}).get("params"),
            submitted_refs=submitted.get(task.id, {}).get("references"))
        references = [ref.model_dump(exclude_none=True) for ref in record.references]
        urls = list(dict.fromkeys([*record.urls, *(url for run in linked for value in (run.urls or [])
                                                 if (url := _safe_url(value)))]))
        label = "分集合成" if task.task_type == "episode_compose" else "视频生成"
        name = f"{fields['project_title']} · {label}" if fields else label
        stamp = task.finished_at or task.updated_at or task.created_at
        lineage = (("asset", task.asset_id) if task.asset_id else ("fragment", task.fragment_id)
                   if task.fragment_id else ("shot", task.shot_id) if task.shot_id else
                   ("project", task.project_id) if task.project_id and (
                       task.task_type == "project_compose_only" or task.task_type == "project_pipeline"
                       and (task.payload or {}).get("phase") in {None, "compose", "produce"}) else None)
        for index, url in enumerate(urls):
            row = video_item(key=f"task:{task.id}:{index}", source_kind="task", source_id=task.id,
                name=name, url=url, cover=record.preview_url,
                params=metadata(record.prompt, record.params, stamp, request=True, references=references),
                references=references, updated_at=stamp, lineage=lineage, **fields)
            if row:
                items.append(row)
    return items, ids


def deduplicate_videos(items):
    """Merge only an entity and one matching task result, not unrelated equal URLs.

    Different shots, assets, fragments and generation attempts can legitimately
    reuse a URL. The newest matching task is represented by its current entity;
    earlier attempts retain their own records even when a provider reused a URL.
    """
    entities = {}
    for item in items:
        if item.media_kind == "video" and item.source_kind != "task" and item._lineage is not None and item.url:
            entities.setdefault((item.project_kind, item.project_id, item._lineage, item.url), item)
    result, consumed = [], set()
    for item in items:
        if item.source_kind == "task" and item._lineage is not None and item.url:
            identity = (item.project_kind, item.project_id, item._lineage, item.url)
            if identity in entities and identity not in consumed:
                consumed.add(identity)
                # Keep immutable accepted generation parameters on the visible entity.
                snapshot = (item.params or {}).get("media_metadata", {})
                if snapshot.get("prompt") or snapshot.get("parameters") or item.references:
                    entity = entities[identity]
                    saved = (entity.params or {}).get("media_metadata", {})
                    if isinstance(saved, dict) and saved.get("source") == "generated":
                        # The stored user prompt retains @asset tokens; the provider prompt has translated them.
                        if isinstance(saved.get("prompt"), str) and saved["prompt"].strip():
                            snapshot = {**snapshot, "prompt": saved["prompt"]}
                        saved_refs = {(ref["kind"], ref["url"], ref["role"]): ref
                                      for ref in public_references(saved.get("references"))}
                        references = [{**saved_refs.get((ref["kind"], ref["url"], ref["role"]), {}), **ref}
                                      for ref in public_references(snapshot.get("references"))]
                        snapshot = {**snapshot, "references": references}
                    entity.params = {**(entity.params or {}), "media_metadata": snapshot}
                    entity.references = item.references
                    entity.cover = entity.cover or item.cover
                continue
        result.append(item)
    return result
