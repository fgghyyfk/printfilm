"""The three durable scheduling lanes. Audio synthesis shares the text lane."""

from typing import Literal

QueueType = Literal["text", "image", "video"]
QUEUE_TYPES: tuple[QueueType, ...] = ("text", "image", "video")
QUEUE_LABELS = {"text": "音频与其他", "image": "图片", "video": "视频"}


def task_queue(task_type: str, payload: dict | None = None) -> QueueType:
    if task_type == "project_pipeline":
        phase = (payload or {}).get("phase")
        if phase == "assets":
            return "image"
        if phase in {"videos", "compose", "produce"}:
            return "video"
    if task_type in {"v1_seedance", "project_compose_only"} or "video" in task_type:
        return "video"
    if "image" in task_type:
        return "image"
    return "text"


def queue_capacity(settings, queue: str) -> int:
    value = getattr(settings, f"task_queue_{queue}_concurrency", None)
    return max(1, int(value if value is not None else settings.task_runtime_max_concurrency))


async def queue_stats(db, settings) -> list[dict]:
    from sqlalchemy import func, select
    from app.models_tasks import TaskRun
    from app.services.tasks.execution_policy import llm_task_condition
    rows = (await db.execute(select(TaskRun.queue_type, TaskRun.status, func.count())
                             .where(~llm_task_condition(TaskRun))
                             .group_by(TaskRun.queue_type, TaskRun.status))).all()
    counts = {(queue, status): count for queue, status, count in rows}
    return [{"name": queue, "label": QUEUE_LABELS[queue], "max_concurrency": queue_capacity(settings, queue),
             "pending": counts.get((queue, "pending"), 0),
             "active": sum(counts.get((queue, state), 0) for state in ("leased", "running", "awaiting_poll")),
             "awaiting_poll": counts.get((queue, "awaiting_poll"), 0)} for queue in QUEUE_TYPES]
