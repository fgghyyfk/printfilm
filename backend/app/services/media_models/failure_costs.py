"""Known costs of failed upstream attempts are platform costs, never retail charges."""
from sqlalchemy import select

from app.models_tasks import TaskEvent


async def record_failed_costs(db, task):
    snapshot = task.billing_snapshot or {}
    if snapshot.get("mode") != "fixed_media":
        return
    from app.services.billing.pricing import parse_upstream_cost_fen
    from app.services.billing.usage import record_line

    events = (await db.execute(select(TaskEvent).where(TaskEvent.task_id == task.id,
        TaskEvent.event_type.in_(("upstream.request.failed", "upstream.request.poll"))))).scalars().all()
    for event in events:
        payload = event.payload or {}
        response = payload.get("response")
        body = response.get("body") if isinstance(response, dict) else None
        if not isinstance(body, dict):
            continue
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        state = str(body.get("status") or data.get("status") or "").lower()
        if event.event_type.endswith(".poll") and state not in {"failed", "failure", "cancelled", "canceled", "expired"}:
            continue
        cost = parse_upstream_cost_fen(body)
        if cost is None:
            cost = parse_upstream_cost_fen(data)
        if cost is None:
            continue
        operation = next((item for item in snapshot["items"] if item["operation_id"] == payload.get("media_operation_id")), None)
        if operation is None:
            operation = next((item for item in snapshot["items"] if payload.get("model") in (item["model_id"], item["route"]["upstream_model"])), None)
        if operation is None:
            continue
        identity = payload.get("provider_task_id") or payload.get("request_id") or str(event.id)
        await record_line(db, user_id=task.requested_by, task_run_id=task.id, domain=task.domain,
            billing_key="seedream" if operation["capability"] == "image" else "seedance2:video0",
            model=operation["model_id"], raw={"usage": {"cost_fen": cost},
                "media_operation_id": operation["operation_id"], "attempt_id": identity,
                "provider_task_id": payload.get("provider_task_id"), "source": "failed_upstream_attempt"})
