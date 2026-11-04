"""科普多镜视频：每镜持久保存原上游任务号和首次提交时间。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models_tasks import TaskRun
from app.services.billing.context import get_current_task_run_id
from app.services.video_recovery import VideoBillingPending, VideoGenerationFailed


async def generate_shot_video(ark, *, shot_id: int, **kwargs):
    task_id = get_current_task_run_id()
    if not task_id:
        return await ark.gen_and_wait_video(**kwargs)
    signature = hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = str(shot_id)
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(TaskRun).where(TaskRun.id == task_id).with_for_update())).scalar_one()
        result = dict(task.result_payload or {})
        tasks = dict(result.get("video_tasks") or {})
        previous = tasks.get(key)
        if previous and previous.get("state") == "done" and not previous.get("provider_task_id"):
            previous = None  # 已确认未接受的旧请求，没有上游任务可复用。
        if previous:
            provider_id = previous.get("provider_task_id")
            if previous.get("input") != signature or not provider_id:
                raise VideoBillingPending("previous_submission_unknown", provider_task_id=provider_id)
            started = datetime.fromisoformat(previous["started_at"])
            if previous.get("endpoint") != ark._url("/contents/generations/tasks"):
                raise VideoBillingPending("provider_route_changed", provider_task_id=provider_id)
        else:
            provider_id = None
            started = datetime.now(UTC)
            tasks[key] = {"input": signature, "started_at": started.isoformat(), "state": "submitting", "endpoint": ark._url("/contents/generations/tasks")}
            result.update(video_tasks=tasks, video_submission_pending=True)
            task.result_payload = result
            await db.commit()

    async def remember(accepted_id):
        nonlocal provider_id
        provider_id = accepted_id
        async with AsyncSessionLocal() as db:
            task = (await db.execute(select(TaskRun).where(TaskRun.id == task_id).with_for_update())).scalar_one()
            result = dict(task.result_payload or {})
            tasks = dict(result.get("video_tasks") or {})
            tasks[key] = {**tasks[key], "provider_task_id": provider_id, "state": "polling"}
            result["video_tasks"] = tasks
            task.result_payload = result
            if task.task_type == "shot_regen_video":
                task.provider_task_id = provider_id
            await db.commit()

    remaining = min(float(get_settings().ark_video_poll_timeout),
                    float(get_settings().video_task_total_timeout) - (datetime.now(UTC) - started).total_seconds())
    if remaining <= 0:
        # 到限只查原任务一次；已经成功可以收尾，仍未知则交人工核对。
        result = await ark.fetch_task_once(provider_id) if provider_id else None
        if result and result.status == "succeeded":
            local, last = await ark.save_video_assets_from_result(result, project_id=kwargs["project_id"], shot_no=kwargs["shot_no"])
            result.provider_task_id = provider_id
            result.last_frame_url = last or result.last_frame_url
            return local, result
        raise VideoBillingPending("total_deadline", provider_task_id=provider_id)
    try:
        async with asyncio.timeout(remaining):
            return await ark.gen_and_wait_video(**kwargs, provider_task_id=provider_id, on_task_created=remember)
    except TimeoutError as exc:
        raise VideoBillingPending("total_deadline", provider_task_id=provider_id) from exc
    except VideoGenerationFailed as exc:
        exc.shot_id = shot_id
        raise
    except VideoBillingPending:
        raise
    except Exception as exc:
        if provider_id:
            raise VideoBillingPending("accepted_result_processing_unknown", provider_task_id=provider_id) from exc
        # 确认未接受的结构/审核拒绝，不保留未知费用标记。
        await finish_shot_video(shot_id)
        raise


async def finish_shot_video(shot_id: int) -> None:
    task_id = get_current_task_run_id()
    if not task_id:
        return
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(TaskRun).where(TaskRun.id == task_id).with_for_update())).scalar_one()
        result = dict(task.result_payload or {})
        tasks = dict(result.get("video_tasks") or {})
        key = str(shot_id)
        if key in tasks:
            tasks[key] = {**tasks[key], "state": "done"}
        result["video_tasks"] = tasks
        if all(row.get("state") == "done" for row in tasks.values()):
            result.pop("video_submission_pending", None)
            result.pop("video_billing_pending", None)
            result["video_terminal_confirmed"] = True
        task.result_payload = result
        await db.commit()
