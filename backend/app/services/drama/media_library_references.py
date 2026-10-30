"""Attach only immutable generation references to authorized visible library rows."""

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models_tasks import TaskRun
from app.schemas_media_references import MediaReferenceOut
from app.services.drama.asset_reference_views import clean_asset_reference_params
from app.services.tasks.generation_history import _submitted_evidence, project_generation
from app.services.tasks.generation_references import enrich_references, public_references


def _saved(item):
    params = item.params if isinstance(item.params, dict) else {}
    metadata = params.get("media_metadata")
    return metadata if isinstance(metadata, dict) else {}


def _plain(item):
    return [ref.model_dump(exclude_none=True) if hasattr(ref, "model_dump") else ref for ref in item.references]


def _set(item, references):
    references = public_references(references)
    item.references = [MediaReferenceOut(**ref) for ref in references]
    metadata = _saved(item)
    if metadata.get("source") == "generated" or "references" in metadata:
        item.params = {**(item.params or {}), "media_metadata": {**metadata, "references": references}}


async def hydrate_library_references(db, items, *, user_id: int, team_id: int | None):
    pending = []
    for item in items:
        metadata = _saved(item)
        if isinstance(metadata.get("references"), list):
            _set(item, metadata["references"])
        elif item.source_kind == "tool" and item._reference_task_id is not None:
            _set(item, _plain(item))
            pending.append(item)
        elif item.references:
            _set(item, _plain(item))
        elif metadata.get("source") != "upload" and (
            item.source_kind == "drama" or item._reference_task_id is not None
        ):
            pending.append(item)
    asset_ids = [item.id for item in pending if item.source_kind == "drama"]
    task_ids = [item._reference_task_id for item in pending if item._reference_task_id is not None]
    targets = []
    if asset_ids:
        targets.append(TaskRun.asset_id.in_(asset_ids))
    if task_ids:
        targets.append(TaskRun.id.in_(task_ids))
    if targets:
        scope = [TaskRun.team_id == team_id] if team_id is not None else [
            TaskRun.team_id.is_(None), TaskRun.requested_by == user_id,
        ]
        tasks = list((await db.scalars(select(TaskRun).where(*scope, or_(*targets),
            TaskRun.status.in_(("succeeded", "failed", "cancelled")))
            .options(selectinload(TaskRun.steps)).order_by(TaskRun.id.desc()))).all())
        submitted = await _submitted_evidence(db, tasks)
        for item in pending:
            for task in tasks:
                linked_tool = item.source_kind == "tool" and item._reference_task_id == task.id
                linked_asset = (item.source_kind == "drama" and task.asset_id == item.id
                                and task.drama_project_id == item.project_id)
                if not (linked_tool or linked_asset):
                    continue
                evidence = submitted.get(task.id, {})
                record = project_generation(task, viewer_id=user_id, kind_override=item.media_kind,
                    submitted_params=evidence.get("params"), submitted_refs=evidence.get("references"))
                # An edited entity can reuse a lineage; require its actual delivered output too.
                if not linked_tool and (not item.url or item.url not in record.urls):
                    continue
                if linked_tool and task.id not in submitted and not record.references:
                    break
                _set(item, [ref.model_dump(exclude_none=True) for ref in record.references])
                break
    # Preserve generation boundaries: equal URLs can represent different explicitly saved assets.
    for item in items:
        project_id = item.project_id if item.project_kind == "drama" else None
        if project_id is not None:
            item.params = await clean_asset_reference_params(db, item.params,
                user_id=user_id, team_id=team_id, project_id=project_id)
            metadata = _saved(item)
            if isinstance(metadata.get("references"), list):
                _set(item, metadata["references"])
                continue
        refs = _plain(item)
        if not refs:
            continue
        enriched = await enrich_references(db, refs, user_id=user_id, team_id=team_id, project_id=project_id)
        _set(item, enriched)
