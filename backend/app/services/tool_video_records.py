"""仅恢复当前页已确认归属的终态工具记录，不修改历史金融数据。"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolRun, User
from app.models_tasks import TaskRun, TaskEvent
from app.services.public_errors import public_message


logger = logging.getLogger(__name__)


async def restore_terminal_tool_records(db: AsyncSession, user_id: int, rows: list[ToolRun]) -> None:
    """页内有限投影；旧成功缺链接仅在原服务商端点有证据时 GET，绝不重提或结算。"""
    from app.services.studio_tools import poll_video_task, update_tool_run_task
    from app.services.ark import get_ark

    pending = [row for row in rows if row.kind == "video" and (row.status in {"queued", "running"} or (row.status == "succeeded" and not row.urls))]
    prepared = []
    queried = 0
    for row in pending:
        try:
            task_id = int((row.params or {}).get("billing_task_id"))
        except (TypeError, ValueError):
            continue
        task = (await db.scalars(select(TaskRun).where(
            TaskRun.id == task_id, TaskRun.requested_by == user_id,
            TaskRun.domain == "studio", TaskRun.task_type == "tool_video",
            TaskRun.provider_task_id == row.task_id,
            TaskRun.status.in_(("succeeded", "failed", "cancelled")),
        ))).one_or_none()
        if not task:
            continue
        data = dict(task.result_payload or {})
        data["status"] = "succeeded" if task.status == "succeeded" else "failed"
        if task.status == "succeeded" and not data.get("urls"):
            # 旧日志没有可信原路由时不拿当前配置猜测；最多恢复三条原 ID。
            accepted = list((await db.scalars(select(TaskEvent).where(
                TaskEvent.task_id == task_id, TaskEvent.event_type == "upstream.request.accepted",
            ).order_by(TaskEvent.id).limit(20))).all())
            trusted = any(
                (event.payload or {}).get("provider") == "tokenfree"
                and (event.payload or {}).get("provider_task_id") == row.task_id
                and (event.payload or {}).get("is_video_submit") is True
                and (event.payload or {}).get("endpoint") == get_ark()._url("/contents/generations/tasks")
                for event in accepted
            )
            if trusted and queried < 3:
                queried += 1
                user = await db.get(User, user_id)
                try:
                    recovered = await poll_video_task(user, row.task_id)
                    if recovered.get("status") == "succeeded" and recovered.get("urls"):
                        data = recovered
                except Exception:  # noqa: BLE001
                    # 成功任务的链接恢复失败不能被改写成生成失败。
                    logger.exception("工具结果链接恢复失败 task_id=%s", task_id)
            if not data.get("urls"):
                data["error"] = public_message("结果待恢复", code="tool_result_restore_pending")
        elif task.status != "succeeded":
            data["error"] = public_message(task.error_message, code=task.error_code)
        prepared.append((task_id, row.task_id, data))
    # 全部网络完成后才取得记录锁；不持锁继续处理下一条媒体。
    for task_id, provider_id, data in prepared:
        await update_tool_run_task(db, user_id, provider_id, data, billing_task_id=task_id)
