"""Unified task platform API: /api/tasks/*."""

from __future__ import annotations
import asyncio
import re

from app.config import get_settings
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_tasks import MockDelayTaskRequest, TaskCreateRequest, TaskListOut, TaskRunOut
from app.services.billing.http import http_exception_for_value_error
from app.services.tasks.service import (
    TaskAccessContext,
    cancel_task_for_user,
    count_active_tasks_for_user,
    create_task,
    get_task_for_user,
    list_tasks_for_user,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
_mock_delay_create_lock = asyncio.Lock()


_MONEY_KEYS = frozenset({"billing", "ledger", "money", "cost", "price", "charge", "charged", "refund", "refunded", "amount", "balance", "frozen", "spent", "reserved", "budget", "fee", "quota"})
_MONEY_MESSAGE = re.compile(r"[¥￥]|\b(?:charge|charged|cost|refund|amount|billing|balance|quota)\b|余额|费用|金额|扣费|退款|额度", re.IGNORECASE)


def _without_money(value):
    """Remove nested monetary metadata without mutating persisted lifecycle events."""
    if isinstance(value, dict):
        return {
            key: _without_money(item)
            for key, item in value.items()
            if not (
                bool(set(re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key)).lower().split("_")) & _MONEY_KEYS)
                or str(key).lower().endswith(("_fen", "_yuan", "_cents"))
                or str(key).lower().startswith("billing_")
            )
        }
    if isinstance(value, list):
        return [_without_money(item) for item in value]
    return value


def _owner_id(task, viewer: User, context: TaskAccessContext) -> int | None:
    access = context.get((task.team_id, int(viewer.id))) if task.team_id is not None else None
    return int(access[0].owner_user_id) if access is not None else None


def public_task(task, *, viewer: User | None = None, owner_user_id: int | None = None) -> TaskRunOut:
    """Team collaborators receive progress and results without internal upstream traces."""
    output = TaskRunOut.model_validate(task)
    if output.team_id is not None:
        output.events = [event for event in output.events if not event.event_type.startswith("upstream.")]
        output.lease_token = None
        for step in output.steps:
            step.input_payload = None
            step.output_payload = None
        can_view_money = viewer is not None and (
            int(viewer.id) == output.requested_by or int(viewer.id) == owner_user_id
        )
        if not can_view_money:
            output.billing_estimate_fen = None
            output.billing_charged_fen = None
            output.billing_refunded_fen = None
            for event in output.events:
                event.payload = _without_money(event.payload)
                if event.message and _MONEY_MESSAGE.search(event.message):
                    event.message = "任务状态已更新"
    return output


# 创建一个延时完成的模拟任务，便于联调任务平台。
@router.post("/mock-delay", response_model=TaskRunOut)
async def create_mock_delay_task(
    body: MockDelayTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRunOut:
    settings = get_settings()
    if not settings.ark_mock and str(user.role or "") != "admin":
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        async with _mock_delay_create_lock:
            active_count = await count_active_tasks_for_user(
                db,
                user,
                domain="tools",
                task_type="mock_delay",
            )
            if active_count >= 3:
                raise HTTPException(status_code=429, detail="模拟延时任务最多同时运行 3 个")
            task = await create_task(
                db,
                user,
                TaskCreateRequest(
                    domain="tools",
                    task_type="mock_delay",
                    dedupe_key=body.dedupe_key,
                    payload={
                        "delay_seconds": body.delay_seconds,
                        "succeed": body.succeed,
                        "result_payload": body.result_payload,
                        "error_message": body.error_message,
                    },
                ),
            )
    except ValueError as exc:
        raise http_exception_for_value_error(exc) from exc
    return public_task(task, viewer=user)


# 查询单个任务详情（含 steps/targets/events）
@router.get("/{task_id}", response_model=TaskRunOut)
async def get_task_run(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRunOut:
    access: TaskAccessContext = {}
    try:
        task = await get_task_for_user(db, user, task_id, access_context=access)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    return public_task(task, viewer=user, owner_user_id=_owner_id(task, user, access))


# 当前用户的统一任务列表，支持 domain/target/project 维度筛选
@router.get("", response_model=TaskListOut)
async def list_task_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    domain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    queue_type: str | None = Query(default=None, pattern="^(text|image|video)$"),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None, ge=1),
    project_id: int | None = Query(default=None, ge=1),
    drama_project_id: int | None = Query(default=None, ge=1),
    team_id: int | None = Query(default=None, ge=1),
    workspace_team_id: int | None = Header(default=None, alias="X-Team-Id", ge=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskListOut:
    if team_id is not None and workspace_team_id is not None and team_id != workspace_team_id:
        raise HTTPException(status_code=422, detail="团队空间参数不一致")
    selected_team_id = team_id if team_id is not None else workspace_team_id
    access: TaskAccessContext = {}
    rows, total = await list_tasks_for_user(
        db,
        user,
        page=page,
        page_size=page_size,
        domain=domain,
        status=status,
        task_type=task_type,
        queue_type=queue_type,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        drama_project_id=drama_project_id,
        team_id=selected_team_id,
        access_context=access,
    )
    return TaskListOut(
        items=[public_task(item, viewer=user, owner_user_id=_owner_id(item, user, access)) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# 统一取消入口：标记 cancel_requested 并等待运行时协作收敛
@router.post("/{task_id}/cancel", response_model=TaskRunOut)
async def cancel_task_run(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRunOut:
    access: TaskAccessContext = {}
    try:
        task = await cancel_task_for_user(db, user, task_id, access_context=access)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise http_exception_for_value_error(exc) from exc
    return public_task(task, viewer=user, owner_user_id=_owner_id(task, user, access))




