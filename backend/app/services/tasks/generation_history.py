"""Read-only generation history across every task domain and legacy tool runs.

The SQL union performs filtering, counts and pagination before loading payloads.
Only explicit public result fields are projected; provider traces and generation
inputs never become downloadable outputs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Project, Shot, ToolRun, User
from app.models_drama import DramaAsset, DramaEpisode, DramaEpisodeFragment, DramaProject
from app.models_tasks import TaskRun
from app.schemas_generation_history import GenerationListOut, GenerationRecordOut
from app.services.tasks.service import TaskAccessContext, _task_team_access

_AUDIO_TYPES = ("voice_synthesis", "voice_preview", "tool_audio", "shot_regen_audio", "project_regen_audio")
_TEXT_TYPES = ("script_summary", "episode_script", "fragment_plan", "seed_assets", "agent_chat",
               "skill_optimize", "voice_prompt", "content_expand", "creation_turn")
_SAFE_PARAMS = frozenset({"prompt", "negative", "model_id", "model", "ratio", "aspect_ratio", "resolution",
                          "duration", "duration_sec", "voice", "voice_id", "phase", "video_mode", "mode",
                          "strength", "motion", "pack", "image_style_id", "episode_number"})
_TEXT_FIELDS = ("text_content", "text", "content", "summary", "script", "body", "answer")
_TEXT_OBJECT_FIELDS = frozenset({*_TEXT_FIELDS, "title", "name", "description", "prompt", "narration",
                                 "number", "episode_number", "sort_order", "duration_sec", "kind", "type"})
_TEXT_COLLECTIONS = ("episodes", "fragments", "assets", "shots", "messages")
_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
_TASK_LABELS = {
    "script_summary": "生成剧本摘要", "episode_script": "生成分集剧本", "fragment_plan": "规划分镜",
    "seed_assets": "提取剧本资产", "asset_image": "生成资产图片", "asset_video": "生成资产视频",
    "fragment_video": "生成分镜视频", "agent_chat": "创作助手对话", "skill_optimize": "优化创作提示词",
    "voice_prompt": "生成配音提示词", "voice_synthesis": "生成角色配音", "episode_compose": "合成分集视频",
    "content_expand": "扩写创作内容", "creation_turn": "创作对话", "voice_preview": "配音试听",
    "shot_regen_image": "重新生成分镜图片", "shot_regen_video": "重新生成分镜视频",
    "shot_regen_audio": "重新生成分镜配音", "project_regen_audio": "重新生成全片配音",
    "project_compose_only": "合成短视频", "tool_image": "生成图片", "tool_video": "生成视频", "tool_audio": "生成音频",
}
_PIPELINE_LABELS = {"script": "生成短视频剧本", "storyboard": "规划短视频分镜", "assets": "生成分镜图片",
                    "videos": "生成分镜视频", "audio": "生成短视频配音", "compose": "合成短视频", "produce": "生成短视频"}


def _public_status(status: str) -> str:
    if status in _TERMINAL_STATUSES:
        return status
    return "queued" if status in {"pending", "leased", "queued"} else "running"


def _kind_expression():
    phase = TaskRun.payload["phase"].as_string()
    return case(
        (TaskRun.task_type.in_(_AUDIO_TYPES), "audio"),
        (TaskRun.task_type.in_(_TEXT_TYPES), "text"),
        (and_(TaskRun.task_type == "project_pipeline", phase == "assets"), "image"),
        (and_(TaskRun.task_type == "project_pipeline", phase == "audio"), "audio"),
        (and_(TaskRun.task_type == "project_pipeline", phase.in_(("script", "text", "plan", "storyboard"))), "text"),
        (TaskRun.task_type.in_(("project_pipeline", "episode_compose", "project_compose_only")), "video"),
        (TaskRun.task_type.contains("video"), "video"),
        (TaskRun.task_type.contains("image"), "image"),
        (TaskRun.queue_type.in_(("image", "video")), TaskRun.queue_type),
        else_="text",
    )


def _generation_kind(task) -> str:
    name = task.task_type
    if name in _AUDIO_TYPES:
        return "audio"
    if name in _TEXT_TYPES:
        return "text"
    if name == "project_pipeline":
        phase = (task.payload or {}).get("phase")
        if phase == "assets":
            return "image"
        if phase == "audio":
            return "audio"
        return "text" if phase in {"script", "text", "plan", "storyboard"} else "video"
    if name in {"episode_compose", "project_compose_only"} or "video" in name:
        return "video"
    if "image" in name:
        return "image"
    return task.queue_type if task.queue_type in {"image", "video"} else "text"


def _tool_link():
    task_id = cast(TaskRun.id, String)
    reference = func.coalesce(ToolRun.params["platform_task_id"].as_string(), ToolRun.params["billing_task_id"].as_string())
    return and_(TaskRun.requested_by == ToolRun.user_id, TaskRun.task_type != "mock_delay", or_(
        task_id == reference,
        ToolRun.task_id == literal("task-") + task_id,
        and_(TaskRun.provider_task_id.is_not(None), TaskRun.domain.in_(("studio", "tools")),
             ToolRun.kind == _kind_expression(), ToolRun.task_id == TaskRun.provider_task_id),
    ))


def _safe_url(value) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 8192 or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password:
        return value
    if not parts.scheme and not parts.netloc and value.startswith(("/media/", "/static/", "/uploads/")) and ".." not in parts.path.split("/"):
        return value
    return None


def _safe_params(data) -> dict:
    return {key: value for key, value in (data.items() if isinstance(data, dict) else [])
            if key in _SAFE_PARAMS and isinstance(value, (str, int, float, bool)) and len(str(value)) <= 7000}


def _result_urls(result, kind: str) -> list[str]:
    if not isinstance(result, dict) or kind == "text":
        return []
    values = []
    for field in ("urls", f"{kind}_urls"):
        if isinstance(result.get(field), list):
            values.extend(result[field])
    values.extend(result.get(field) for field in ("url", f"{kind}_url", "output_url"))
    # OpenAI-compatible image results have data:[{url:...}]. Do not recurse into
    # arbitrary provider/request objects, which can contain reference media.
    if kind == "image" and isinstance(result.get("data"), list):
        values.extend(item.get("url") for item in result["data"] if isinstance(item, dict))
    return list(dict.fromkeys(url for item in values if (url := _safe_url(item))))


def _text_output(result) -> str:
    if not isinstance(result, dict):
        return ""
    for field in _TEXT_FIELDS:
        if isinstance(result.get(field), str):
            return result[field][:50000]
    for field in _TEXT_COLLECTIONS:
        if isinstance(result.get(field), list):
            records = [{key: value for key, value in item.items()
                        if key in _TEXT_OBJECT_FIELDS and isinstance(value, (str, int, float, bool))}
                       for item in result[field][:200] if isinstance(item, dict)]
            if any(records):
                return json.dumps(records, ensure_ascii=False, indent=2)[:50000]
    return ""


def project_generation(task, *, viewer_id: int, owner_user_id: int | None = None,
                       linked_tool: ToolRun | None = None, fallback: dict | None = None) -> GenerationRecordOut:
    """Project only safe public fields from already persisted task data."""
    kind = _generation_kind(task)
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    outputs = [result, *(step.output_payload for step in getattr(task, "steps", [])
                         if isinstance(step.output_payload, dict))]
    urls = next((values for output in outputs if (values := _result_urls(output, kind))), [])
    if not urls and linked_tool is not None:
        urls = [url for value in (linked_tool.urls or []) if (url := _safe_url(value))]
    if not urls and fallback and task.status == "succeeded":
        urls = _result_urls(fallback, kind)
    preview = next((url for output in outputs if (url := _safe_url(output.get("preview_url")))), None)
    if linked_tool is not None and not preview:
        preview = _safe_url(linked_tool.preview_url)
    if not preview and kind == "image" and urls:
        preview = urls[0]
    params = {**_safe_params(linked_tool.params if linked_tool is not None else {}), **_safe_params(task.payload)}
    prompt = str(params.get("prompt") or (linked_tool.prompt if linked_tool is not None else "") or "")
    if not prompt:
        prompt = next((str((task.payload or {}).get(key) or "") for key in ("text", "instruction", "draft", "name")
                       if isinstance((task.payload or {}).get(key), str) and (task.payload or {}).get(key)), "")
    if not prompt:
        prompt = (_PIPELINE_LABELS.get((task.payload or {}).get("phase"), "生成 AI 短视频")
                  if task.task_type == "project_pipeline" else _TASK_LABELS.get(task.task_type, "生成任务"))
    can_view_money = task.team_id is None or viewer_id in {task.requested_by, owner_user_id}
    created_at = getattr(task, "created_at", None)
    return GenerationRecordOut(
        id=task.id, source="task", record_key=f"task:{task.id}",
        tool_id=linked_tool.tool_id if linked_tool is not None else str((task.payload or {}).get("tool_id") or task.task_type),
        kind=kind, status=_public_status(task.status),
        domain=task.domain, task_type=task.task_type, prompt=prompt[:7000], urls=urls, preview_url=preview,
        text_content=next((text for output in outputs if (text := _text_output(output))), "") if kind == "text" else "",
        task_id=f"task-{task.id}", params=params, error=getattr(task, "error_message", None),
        created_at=created_at.isoformat() if created_at else "", platform_task_id=task.id,
        provider_task_id=getattr(task, "provider_task_id", None), provider_name=getattr(task, "provider_name", None),
        provider_status=getattr(task, "provider_status", None), queue_type=task.queue_type,
        started_at=getattr(task, "started_at", None), finished_at=getattr(task, "finished_at", None),
        billing_status=getattr(task, "billing_status", None) or "none",
        billing_charged_fen=int(getattr(task, "billing_charged_fen", 0) or 0) if can_view_money else None,
    )


def _legacy_projection(row: ToolRun) -> GenerationRecordOut:
    params = _safe_params(row.params)
    return GenerationRecordOut(
        id=row.id, source="tool", record_key=f"tool:{row.id}", tool_id=row.tool_id,
        kind=row.kind if row.kind in {"video", "image", "audio"} else "text", status=_public_status(row.status),
        domain="studio", prompt=row.prompt or "", urls=[url for value in (row.urls or []) if (url := _safe_url(value))],
        preview_url=_safe_url(row.preview_url), params=params, task_id=row.task_id,
        provider_task_id=str((row.params or {}).get("provider_task_id") or row.task_id or "") or None,
        error=row.error, created_at=row.created_at.isoformat() if row.created_at else "",
    )


async def _workspace(db, user: User, team_id: int | None, context: TaskAccessContext):
    if team_id is not None:
        team, _ = await _task_team_access(db, team_id, int(user.id), context=context)
        return [TaskRun.team_id == team_id], int(team.owner_user_id)
    return [TaskRun.requested_by == user.id, TaskRun.team_id.is_(None)], None


def _entity_matches_task(task, project, *, drama: bool) -> bool:
    expected_project_id = task.drama_project_id if drama else task.project_id
    if project.id != expected_project_id or getattr(project, "team_id", None) != task.team_id:
        return False
    return task.team_id is not None or project.user_id == task.requested_by


def _entity_is_current(entity, task) -> bool:
    """An entity edited after completion is no longer evidence of this output."""
    updated = getattr(entity, "updated_at", None)
    finished = task.finished_at
    if updated is None:
        return True
    if finished is None:
        return False
    updated = updated.replace(tzinfo=UTC) if updated.tzinfo is None else updated
    finished = finished.replace(tzinfo=UTC) if finished.tzinfo is None else finished
    return updated <= finished + timedelta(seconds=2)


async def _latest_target_tasks(db, tasks, target_name: str) -> set[int]:
    target = getattr(TaskRun, target_name)
    ids = {getattr(task, target_name) for task in tasks if getattr(task, target_name) is not None}
    if not ids:
        return set()
    kind = _kind_expression()
    return set((await db.scalars(select(func.max(TaskRun.id)).where(target.in_(ids), TaskRun.status == "succeeded")
                                .group_by(target, kind))).all())


async def _legacy_entity_outputs(db, tasks) -> dict[int, dict]:
    """Recover legacy artifacts only from the latest matching owned entity.

    New work stores immutable URLs on the task itself. Older tasks did not, so
    only the latest successful generation may use an entity's current value;
    previous attempts stay visible without being assigned a later result.
    """
    candidates = [task for task in tasks if task.status == "succeeded" and _generation_kind(task) != "text"
                  and not any(_result_urls(value, _generation_kind(task)) for value in [task.result_payload,
                              *(step.output_payload for step in task.steps)])]
    outputs = {}
    asset_tasks = [task for task in candidates if task.asset_id and task.task_type in {"asset_image", "asset_video"}]
    if asset_tasks:
        latest = await _latest_target_tasks(db, asset_tasks, "asset_id")
        rows = (await db.execute(select(DramaAsset, DramaProject).join(DramaProject, DramaAsset.project_id == DramaProject.id)
                                 .where(DramaAsset.id.in_({task.asset_id for task in asset_tasks})))).all()
        entities = {asset.id: (asset, project) for asset, project in rows}
        for task in asset_tasks:
            asset, project = entities.get(task.asset_id, (None, None))
            if (asset is not None and task.id in latest and asset.asset_type == _generation_kind(task)
                    and _entity_matches_task(task, project, drama=True) and _entity_is_current(asset, task)):
                outputs[task.id] = {"urls": [asset.url]}
    fragment_tasks = [task for task in candidates if task.fragment_id and task.task_type == "fragment_video"]
    if fragment_tasks:
        latest = await _latest_target_tasks(db, fragment_tasks, "fragment_id")
        rows = (await db.execute(select(DramaEpisodeFragment, DramaEpisode, DramaProject)
            .join(DramaEpisode, DramaEpisodeFragment.episode_id == DramaEpisode.id)
            .join(DramaProject, DramaEpisode.project_id == DramaProject.id)
            .where(DramaEpisodeFragment.id.in_({task.fragment_id for task in fragment_tasks})))).all()
        entities = {fragment.id: (fragment, episode, project) for fragment, episode, project in rows}
        for task in fragment_tasks:
            fragment, episode, project = entities.get(task.fragment_id, (None, None, None))
            if (fragment is not None and task.id in latest and (task.episode_id is None or task.episode_id == episode.id)
                    and _entity_matches_task(task, project, drama=True) and _entity_is_current(fragment, task)):
                outputs[task.id] = {"urls": [fragment.video]}
    shot_tasks = [task for task in candidates if task.shot_id and task.task_type.startswith("shot_regen_")]
    if shot_tasks:
        latest = await _latest_target_tasks(db, shot_tasks, "shot_id")
        rows = (await db.execute(select(Shot, Project).join(Project, Shot.project_id == Project.id)
                                .where(Shot.id.in_({task.shot_id for task in shot_tasks})))).all()
        entities = {shot.id: (shot, project) for shot, project in rows}
        for task in shot_tasks:
            shot, project = entities.get(task.shot_id, (None, None))
            if shot is not None and task.id in latest and _entity_matches_task(task, project, drama=False):
                outputs[task.id] = {"urls": [getattr(shot, f"{_generation_kind(task)}_url", None)]}
    project_tasks = [task for task in candidates if task.project_id and task.task_type in {
        "project_pipeline", "project_compose_only", "project_regen_audio"}]
    if project_tasks:
        latest = await _latest_target_tasks(db, project_tasks, "project_id")
        ids = {task.project_id for task in project_tasks}
        projects = {project.id: project for project in (await db.scalars(select(Project).where(Project.id.in_(ids)))).all()}
        shots = (await db.scalars(select(Shot).where(Shot.project_id.in_(ids)).order_by(Shot.shot_no))).all()
        for task in project_tasks:
            project = projects.get(task.project_id)
            if (project is None or task.id not in latest or not _entity_matches_task(task, project, drama=False)
                    or not _entity_is_current(project, task)):
                continue
            phase = (task.payload or {}).get("phase")
            kind = _generation_kind(task)
            if kind == "video" and (task.task_type == "project_compose_only" or phase in {None, "compose", "produce"}):
                outputs[task.id] = {"urls": [project.final_video_url]}
            else:
                outputs[task.id] = {"urls": [getattr(shot, f"{kind}_url", None) for shot in shots if shot.project_id == project.id]}
    return outputs


async def _load_records(db, user, references, owner_user_id):
    task_ids = [row.id for row in references if row.source == "task"]
    tool_ids = [row.id for row in references if row.source == "tool"]
    tasks = list((await db.scalars(select(TaskRun).where(TaskRun.id.in_(task_ids))
                                 .options(selectinload(TaskRun.steps)))).all()) if task_ids else []
    tools = list((await db.scalars(select(ToolRun).where(ToolRun.id.in_(tool_ids)))).all()) if tool_ids else []
    linked = (await db.execute(select(TaskRun.id, ToolRun).join(ToolRun, _tool_link())
                              .where(TaskRun.id.in_(task_ids)).order_by(ToolRun.id.desc()))).all() if task_ids else []
    linked_by_task = {}
    for task_id, tool in linked:
        linked_by_task.setdefault(task_id, tool)
    fallback = await _legacy_entity_outputs(db, tasks)
    records = {f"task:{task.id}": project_generation(task, viewer_id=user.id, owner_user_id=owner_user_id,
                                                    linked_tool=linked_by_task.get(task.id), fallback=fallback.get(task.id)) for task in tasks}
    records.update({f"tool:{tool.id}": _legacy_projection(tool) for tool in tools})
    return [records[f"{row.source}:{row.id}"] for row in references]


async def list_generations(db: AsyncSession, user: User, *, page: int = 1, page_size: int = 20,
                           kind: str | None = None, status: str | None = None, task_id: str | None = None,
                           created_from: datetime | None = None, created_to: datetime | None = None,
                           team_id: int | None = None) -> GenerationListOut:
    scope, owner_user_id = await _workspace(db, user, team_id, {})
    task_query = select(literal("task").label("source"), TaskRun.id.label("id"),
                        TaskRun.created_at.label("created_at"), TaskRun.status.label("status"),
                        _kind_expression().label("kind")).where(*scope, TaskRun.task_type != "mock_delay")
    tool_query = select(literal("tool").label("source"), ToolRun.id.label("id"),
                        ToolRun.created_at.label("created_at"), ToolRun.status.label("status"),
                        ToolRun.kind.label("kind")).where(ToolRun.user_id == user.id,
                            ~exists(select(TaskRun.id).where(_tool_link())))
    if team_id is not None:
        tool_query = tool_query.where(literal(False))
    if task_id and (value := task_id.strip()):
        linked_id = exists(select(ToolRun.id).where(_tool_link(), or_(cast(ToolRun.id, String) == value, ToolRun.task_id == value)))
        task_query = task_query.where(or_(cast(TaskRun.id, String) == value, TaskRun.provider_task_id == value,
                                         literal("task-") + cast(TaskRun.id, String) == value, linked_id))
        tool_query = tool_query.where(or_(cast(ToolRun.id, String) == value, ToolRun.task_id == value,
                                         ToolRun.params["provider_task_id"].as_string() == value))
    history = union_all(task_query, tool_query).subquery()
    bucket = case((history.c.status.in_(_TERMINAL_STATUSES), history.c.status), else_="processing")
    filters = []
    if kind:
        filters.append(history.c.kind == kind)
    if created_from:
        filters.append(history.c.created_at >= created_from)
    if created_to:
        filters.append(history.c.created_at <= created_to)
    counts = {name: 0 for name in ("processing", "succeeded", "failed", "cancelled")}
    counts.update(dict((await db.execute(select(bucket, func.count()).select_from(history)
                                        .where(*filters).group_by(bucket))).all()))
    counts["all"] = sum(counts.values())
    total = counts[status] if status else counts["all"]
    if status:
        filters.append(bucket == status)
    rows = (await db.execute(select(history.c.source, history.c.id).where(*filters)
                            .order_by(history.c.created_at.desc(), history.c.id.desc(), history.c.source.desc())
                            .offset((page - 1) * page_size).limit(page_size))).all()
    return GenerationListOut(items=await _load_records(db, user, rows, owner_user_id),
                             total=total, page=page, page_size=page_size, counts=counts)


async def get_generation(db: AsyncSession, user: User, task_id: int, *, team_id: int | None = None) -> GenerationRecordOut:
    scope, owner_user_id = await _workspace(db, user, team_id, {})
    row = (await db.execute(select(literal("task").label("source"), TaskRun.id.label("id"))
                            .where(*scope, TaskRun.id == task_id, TaskRun.task_type != "mock_delay"))).first()
    if row is None:
        raise LookupError("任务不存在")
    return (await _load_records(db, user, [row], owner_user_id))[0]
