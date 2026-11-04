"""按本次实际分镜集合和模型规格估价，不创建任务或修改钱包。"""
from app.models_tasks import TaskRun
from app.services.billing.estimates import estimate_task_fen
from app.services.billing.settlement import billing_active
from app.services.drama.fragment_content_duration import resolve_seedance_duration_from_content
from app.services.drama.output_settings import resolve_episode_video_output
from app.services.seedance_resolutions import clamp_video_duration, clamp_video_resolution


def fragment_video_task_inputs(episode, project, fragment, model_id: str | None) -> dict:
    """报价和入队共用时长及规格，准备阶段沿用此快照。"""
    ratio, resolution = resolve_episode_video_output(episode.params, project.params)
    duration = resolve_seedance_duration_from_content(fragment.content or "", fallback=int(fragment.duration_sec or 8))
    return {
        "project_id": project.id, "episode_id": episode.id, "fragment_ids": [fragment.id],
        "duration_sec": clamp_video_duration(model_id, duration),
        "model_id": (model_id or "").strip() or None,
        "resolution": clamp_video_resolution(model_id, resolution), "aspect_ratio": ratio,
    }


async def quote_fragment_videos(db, user, episode, project, fragments: list, model_id: str | None) -> dict:
    """逐镜采用真实时长、模型、清晰度估价，关闭计费时不请求价目。"""
    rows = []
    for fragment in fragments:
        payload = fragment_video_task_inputs(episode, project, fragment, model_id)
        amount = await estimate_task_fen(db, TaskRun(
            domain="drama", task_type="fragment_video", requested_by=user.id,
            drama_project_id=project.id, episode_id=episode.id, fragment_id=fragment.id, payload=payload,
        )) if billing_active(user) else 0
        rows.append({"fragment_id": fragment.id, "estimate_fen": amount, **payload})
    return {"fragment_ids": [fragment.id for fragment in fragments], "count": len(rows),
            "estimate_fen": sum(row["estimate_fen"] for row in rows), "fragments": rows}
