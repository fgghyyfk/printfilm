"""Task-local immutable media operations and credential-free route snapshots."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.schemas_routing import ResolvedModelRoute
from app.services.logical_model_router import resolve_logical_model, resolve_logical_model_id, resolve_upstream_model
from app.services.model_settings import get_routing_snapshot
from app.services.model_availability import is_managed_model, is_model_enabled
from .prices import resolve_strategy
from . import get_strategy

current_snapshot: ContextVar[dict | None] = ContextVar("media_task_snapshot", default=None)
current_operation: ContextVar[dict | None] = ContextVar("media_operation", default=None)


def _public_route(route: dict) -> dict:
    address = urlsplit(route["base_url"])
    if address.username or address.password or address.query or address.fragment:
        raise ValueError("route: 渠道地址不能包含凭据或查询参数，请使用专用 API Key 字段")
    return route


def pin_route(capability: str, model_id: str) -> dict:
    settings = get_settings()
    snapshot = get_routing_snapshot()
    models = getattr(snapshot, "logical_models", [])
    if not is_model_enabled(capability, model_id, models):
        raise ValueError(f"model: {model_id} 已停用")
    strategy = resolve_strategy(model_id)
    logical = next((model for model in getattr(snapshot, "logical_models", []) if model.id.lower() == model_id.lower()), None)
    route = resolve_logical_model(capability, model_id)
    if route is None and logical is None and model_id != strategy.model_id:
        route = resolve_logical_model(capability, strategy.model_id)
    if route:
        if get_strategy(route.upstream_model).model_id != strategy.model_id:
            raise ValueError("route: 上游绑定与所选模型策略不匹配")
        return _public_route(route.model_dump(exclude={"api_key"}))
    if is_managed_model(capability, model_id, models):
        raise ValueError(f"route: {model_id} 没有启用的可用渠道绑定")
    if snapshot.channels and not (settings.ark_mock or settings.local_media_mock):
        raise ValueError(f"route: {model_id} 没有可用渠道，不会自动切换模型")
    return _public_route({"capability": capability, "logical_model_id": model_id,
            "upstream_model": resolve_strategy(model_id).model_id,
            "channel_id": "__runtime__", "channel_name": "Runtime",
            "base_url": settings.ark_base_url, "protocol": "openai", "api_format": "openai"})


def restore_route(saved: dict) -> ResolvedModelRoute:
    settings = get_settings()
    if saved["channel_id"] == "__runtime__":
        key = settings.ark_api_key
    else:
        channel = next((x for x in get_routing_snapshot().channels if x.id == saved["channel_id"]), None)
        if not channel or not channel.enabled or not channel.api_key:
            raise RuntimeError("固定渠道已停用或凭证不可用，不会自动切换路由")
        key = channel.api_key
    return ResolvedModelRoute(**saved, api_key=key or "")


def operation_for(capability: str, *, target: int | None = None, shot_no: int | None = None, model: str | None = None) -> dict | None:
    snapshot = current_snapshot.get()
    if not snapshot:
        return None
    items = [x for x in snapshot.get("items", []) if x["capability"] == capability]
    if target is not None or shot_no is not None:
        field, value = ("shot_no", shot_no) if shot_no is not None else ("target_id", target)
        exact = [x for x in items if x.get(field) == value]
        if exact:
            return exact[0]
        if any(x.get("target_id") is not None or x.get("shot_no") is not None for x in items):
            raise RuntimeError(f"{capability}: 此任务没有预留指定资产或镜头")
    current = current_operation.get()
    if target is None and shot_no is None and current and any(x["operation_id"] == current["operation_id"] for x in items):
        return current
    if model:
        exact = [x for x in items if model in (x["model_id"], x["route"]["upstream_model"], x["route"]["logical_model_id"])]
        if exact:
            return exact[0]
    if items:
        return items[0]
    raise RuntimeError(f"{capability}: 此任务没有预留该生成操作")


def execution_params(capability: str, params: dict, *, target: int | None = None, shot_no: int | None = None, model: str | None = None) -> tuple[Any, dict, ResolvedModelRoute | None]:
    operation = operation_for(capability, target=target, shot_no=shot_no, model=model)
    if operation:
        strategy = get_strategy(operation["strategy_id"]).with_reference_limits(operation.get("reference_limits"))
        # Admission owns billing dimensions. Runtime supplies prompt and actual reference URLs.
        merged = {**params, **{key: operation["params"][key] for key in ("resolution", "duration", "aspect_ratio") if key in operation["params"]}}
        validated = strategy.normalize_and_validate(merged)
        current_operation.set(operation)
        return strategy, validated, restore_route(operation["route"])
    resolve_upstream_model(capability, model)
    selected = model or resolve_logical_model_id(capability, None) or (
        get_settings().model_image if capability == "image" else get_settings().model_video
    )
    strategy = resolve_strategy(selected)
    validated = strategy.normalize_and_validate(params)
    return strategy, validated, None


def stamp_usage(raw: dict | None, *, delivery: str | None = None) -> dict:
    result = dict(raw or {})
    operation = current_operation.get()
    if operation:
        result["media_operation_id"] = operation["operation_id"]
    if delivery:
        result["delivery_id"] = delivery
    return result


async def existing_provider_task() -> str | None:
    from app.database import AsyncSessionLocal
    from app.models_tasks import TaskRun
    from app.services.billing.context import get_current_task_run_id
    task_id = get_current_task_run_id()
    operation = current_operation.get()
    if not task_id or not operation or operation["capability"] != "video":
        return None
    async with AsyncSessionLocal() as db:
        task = await db.get(TaskRun, task_id)
        if task:
            return (task.media_provider_tasks or {}).get(operation["operation_id"])
    return None
