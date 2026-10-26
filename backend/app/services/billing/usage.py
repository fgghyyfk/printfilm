# -*- coding: utf-8 -*-
"""用量行写入：唯一写 usage_events 的入口。"""
from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models import UsageEvent
from app.services.billing.context import get_current_task_run_id
from app.services.billing.pricing import billing_key_to_capability, charge_fen_for_usage


async def record_line(
    db: AsyncSession,
    *,
    user_id: int,
    billing_key: str,
    model: str = "",
    tokens: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated: bool = False,
    raw: dict | None = None,
    project_id: int | None = None,
    drama_project_id: int | None = None,
    shot_id: int | None = None,
    provider: str = "ark",
    task_run_id: int | None = None,
    domain: str | None = None,
) -> UsageEvent:
    s = get_settings()
    if getattr(s, "local_media_mock", False) and billing_key_to_capability(billing_key) in {"image", "video", "tts"}:
        raw = {**(raw or {}), "simulated": True}
    from app.services.tokenfree_pricing import ensure_official_rates

    tid = task_run_id if task_run_id is not None else get_current_task_run_id()
    team_id = None
    task = None
    if tid is not None:
        from app.models_tasks import TaskRun
        task = await db.get(TaskRun, int(tid))
        if task is not None:
            user_id = int(task.requested_by)
            team_id = task.team_id
            drama_project_id = task.drama_project_id or drama_project_id
    if not task or (task.billing_snapshot or {}).get("mode") != "fixed_media" or billing_key_to_capability(billing_key) not in {"image", "video"}:
        await ensure_official_rates(s)
    total = int(tokens) or (int(prompt_tokens) + int(completion_tokens))
    if total <= 0:
        if billing_key == "llm_chat":
            total = s.billing_est_llm_tokens
            estimated = True
        elif billing_key == "seedream":
            # TokenFree /responses 常回 0 token；按张价在 charge_fen_for_usage 计算
            estimated = True
        elif billing_key == "tts":
            total = s.billing_est_tts_tokens
            estimated = True
        elif billing_key.startswith("seedance"):
            total = s.billing_est_seedance_tokens_per_sec * 5
            estimated = True
    detail = None
    delivery_key = None
    cost_status = None
    snapshot = task.billing_snapshot if task is not None else None
    if snapshot and snapshot.get("mode") == "fixed_media" and billing_key_to_capability(billing_key) in {"image", "video"}:
        # Serialize delivery admission, including the uniqueness check and quantity cap.
        # Lock fresh billing flags without expiring the executor's loaded step relationships.
        flags = (await db.execute(select(type(task).billing_status, type(task).billing_enabled_snapshot)
            .where(type(task).id == task.id).with_for_update())).one()
        task.billing_status = flags.billing_status
        task.billing_enabled_snapshot = flags.billing_enabled_snapshot
        facts = dict(raw or {})
        facts = {**(facts.get("usage") or {}), **facts}
        capability = billing_key_to_capability(billing_key)
        candidates = [x for x in snapshot["items"] if x["capability"] == capability]
        operation = next((x for x in candidates if x["operation_id"] == facts.get("media_operation_id")), None)
        if operation is None and shot_id:
            operation = next((x for x in candidates if x.get("target_id") == shot_id), None)
        if operation is None and len(candidates) == 1:
            operation = candidates[0]
        if operation is None:
            raise ValueError("billing: 无法匹配已预留的生成操作")
        delivery = facts.get("delivery_id")
        identity = delivery or facts.get("attempt_id")
        if identity:
            digest = hashlib.sha256(str(identity).encode()).hexdigest()
            delivery_key = f"{'media' if delivery else 'media-cost'}:{task.id}:{operation['operation_id']}:{digest}"
            existing = (await db.execute(select(UsageEvent).where(UsageEvent.delivery_key == delivery_key))).scalar_one_or_none()
            if existing:
                return existing
        if task.billing_status == "settled":
            raise ValueError("billing: 已结算任务不能新增交付，请创建新的生成任务")
        from app.services.billing.pricing import parse_upstream_cost_fen
        parsed_cost = parse_upstream_cost_fen(raw, settings=s)
        cost = parsed_cost or 0
        cost_status = "verified" if parsed_cost is not None else "pending"
        charge = operation["unit_charge_fen"] if delivery else 0
        if task.billing_status == "skipped" or task.billing_enabled_snapshot is False:
            charge = 0
        if delivery:
            delivered = list((await db.execute(select(UsageEvent).where(UsageEvent.task_run_id == task.id, UsageEvent.delivery_key.is_not(None)))).scalars())
            count = sum(1 for event in delivered if (event.billing_detail or {}).get("operation_id") == operation["operation_id"] and (event.billing_detail or {}).get("delivered"))
            if count >= operation["params"]["count"]:
                raise ValueError("billing: 交付数量超过任务已预留数量")
        model = operation["model_id"]
        detail = {"mode": "fixed_media", "price_version": snapshot["price_version"],
                  "operation_id": operation["operation_id"], "model_id": model,
                  "resolution": operation["resolution"], "duration": operation.get("duration"),
                  "unit_price_yuan": operation["unit_price_yuan"], "delivered": bool(delivery),
                  "billing_enabled": task.billing_status != "skipped" and task.billing_enabled_snapshot is not False,
                  "route": operation["route"]}
        raw = {**(raw or {}), "billing_basis": "fixed_media"}
        from_upstream = parsed_cost is not None
        estimated = False  # Retail price is exact even when upstream cost is pending.
    else:
        cost, charge, from_upstream = charge_fen_for_usage(
            total, billing_key, raw_usage=raw, settings=s, model=model
        )
    # 仅在确有上游 token / quota 时取消「估算」；官方表达式按秒估仍标估算
    usage_parsed = raw.get("usage") if isinstance(raw, dict) and isinstance(raw.get("usage"), dict) else (
        raw if isinstance(raw, dict) else {}
    )
    real_tok = 0
    # 本地按时长估算写入的 tokens 带 tokens_estimated 标记，不算上游实测
    if isinstance(usage_parsed, dict) and not usage_parsed.get("tokens_estimated"):
        for key in ("tokens", "total_tokens", "completion_tokens"):
            try:
                real_tok = int(usage_parsed.get(key) or 0)
            except (TypeError, ValueError):
                real_tok = 0
            if real_tok > 0:
                break
    if from_upstream or real_tok > 0:
        estimated = False
    elif total > 0 and not estimated:
        pass
    capability = billing_key_to_capability(billing_key)
    # 显式写墙钟时间：长任务会话里 server_default=now() 会落成事务起点，用量明细按时间/日期筛会漏看
    ev = UsageEvent(
        user_id=user_id,
        team_id=team_id,
        billing_payer_user_id=task.billing_payer_user_id if task is not None else None,
        project_id=project_id,
        drama_project_id=drama_project_id,
        task_run_id=tid,
        domain=domain,
        capability=capability,
        shot_id=shot_id,
        provider=provider,
        billing_key=billing_key,
        model=model or "",
        prompt_tokens=int(prompt_tokens),
        completion_tokens=int(completion_tokens),
        total_tokens=total,
        cost_fen=cost,
        cost_status=cost_status,
        billing_detail=detail,
        delivery_key=delivery_key,
        charge_fen=0 if team_id is not None and task.billing_enabled_snapshot is False else charge,
        estimated=estimated,
        settled=False,
        raw_usage_json=json.dumps(raw, ensure_ascii=False)[:4000] if raw else None,
        created_at=datetime.now(UTC),
    )
    db.add(ev)
    await db.flush()
    return ev


async def record_llm_chat_line(
    db: AsyncSession,
    *,
    user_id: int,
    domain: str,
    drama_project_id: int | None = None,
    project_id: int | None = None,
    tokens: int | None = None,
) -> UsageEvent | None:
    """在 billing_scope 内记录一次 LLM 调用；无 scope 时跳过（由调用方聚合计费）。"""
    if get_current_task_run_id() is None:
        return None
    s = get_settings()
    return await record_line(
        db,
        user_id=user_id,
        billing_key="llm_chat",
        model=s.model_llm,
        tokens=int(tokens or 0),
        estimated=True,
        project_id=project_id,
        drama_project_id=drama_project_id,
        domain=domain,
    )


# 兼容旧调用名
async def record_usage(
    db: AsyncSession,
    *,
    user_id: int,
    project_id: int | None,
    billing_key: str,
    model: str = "",
    tokens: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated: bool = False,
    raw: dict | None = None,
    shot_id: int | None = None,
    provider: str = "ark",
    drama_project_id: int | None = None,
    domain: str | None = None,
    task_run_id: int | None = None,
) -> UsageEvent:
    return await record_line(
        db,
        user_id=user_id,
        billing_key=billing_key,
        model=model,
        tokens=tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated=estimated,
        raw=raw,
        project_id=project_id,
        drama_project_id=drama_project_id,
        shot_id=shot_id,
        provider=provider,
        domain=domain,
        task_run_id=task_run_id,
    )
