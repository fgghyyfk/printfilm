"""TTS 本次响应元数据与成功音频的唯一记账入口。"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models_tasks import TaskRun
from app.services.billing.context import get_current_task_run_id
from app.services.billing.pricing import charge_fen_for_usage, parse_usage_dict
from app.services.kepu_avatar_billing import speech_cost_fen, volc_tts_cost_fen

logger = logging.getLogger(__name__)

# 低层仅通知本次已返回音频；回调由 gateway 创建，不持有跨调用状态。
AudioResponseHook = Callable[[dict], Awaitable[None]]
_USAGE_FIELDS = (
    "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
    "generated_tokens", "quota", "quota_consumed", "consumed_quota", "cost_fen", "cost_cents",
    "cost_yuan", "total_cost_yuan", "cost", "total_cost", "amount",
)


@dataclass(frozen=True)
class TTSResult:
    """最终可用音频及本次实际成功 provider 的局部结果。"""
    url: str
    provider: str
    model: str
    is_fallback: bool = False
    usage_recorded: bool = False


class TTSBillingPending(RuntimeError):
    """音频已付费生成、用量尚未落账，持有有限快照供原任务待核对。"""

    def __init__(self, snapshot: dict):
        """只携带计费字段，媒体 URL 在 gateway 完成本地写入后补充。"""
        super().__init__("配音已生成，计费记录待核对，请勿重新生成")
        self.snapshot = snapshot


def mark_tts_billing_pending(task: TaskRun, exc: Exception) -> bool:
    """由原任务事务保存待核对快照，任何结算入口都须保留已冻结金额。"""
    if isinstance(exc, TTSBillingPending):
        task.result_payload = {**(task.result_payload or {}), "tts_billing_pending": exc.snapshot}
        return True
    return False


def tts_usage_fields(payload: dict | None) -> dict:
    """只保留有限的数值计费字段，绝不保存音频或整份上游响应。"""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key in _USAGE_FIELDS:
        value = payload.get(key)
        if not isinstance(value, (int, float, str)) or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (ValueError, OverflowError):
            continue
        if math.isfinite(number) and 0 <= number <= 10**15:
            result[key] = int(number) if number.is_integer() else number
    usage = payload.get("usage")
    if isinstance(usage, dict):
        result["usage"] = tts_usage_fields({key: usage[key] for key in _USAGE_FIELDS if key in usage})
    return result


def tts_response_usage(text: str) -> dict:
    """读取 JSON 或 SSE 的用量块，分片只合并计费字段。"""
    try:
        return tts_usage_fields(json.loads(text))
    except (ValueError, TypeError):
        pass
    result = {}
    for line in text.splitlines():
        if not line.strip().startswith("data:"):
            continue
        try:
            fields = tts_usage_fields(json.loads(line.strip()[5:]))
        except (ValueError, TypeError):
            continue
        usage = {**result.get("usage", {}), **fields.pop("usage", {})}
        result.update(fields)
        if usage:
            result["usage"] = usage
    return result


async def record_tts_response(
    provider: str, model: str, *, chars: int, tokens: int = 0, shot_id: int | None = None,
    raw: dict | None = None,
) -> bool:
    """已成功的付费响应记一次；只重试本地落账，持续失败由原任务待核对。"""
    from app.services.billing import record_line

    task_id = get_current_task_run_id()
    if task_id is None:
        return False
    fields = {**tts_usage_fields(raw), "provider": provider, "model": model[:200]}
    billing_key = "tts"
    if provider in {"volc", "avatar_ai"}:
        billing_key = "avatar_tts"
        fields["cost_fen"] = volc_tts_cost_fen(chars) if provider == "volc" else speech_cost_fen(chars)
        if "usage" in fields:
            # 这些直连服务继续按原字符单价；附带 token 不得把字符成本覆盖成通用估价。
            fields["usage"]["cost_fen"] = fields["cost_fen"]
        fields["chars"] = chars
    usage = parse_usage_dict(fields)

    values = dict(
        billing_key=billing_key, model=model, tokens=usage["total_tokens"] or tokens,
        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
        estimated=True, raw=fields, provider=provider,
    )

    async def write(db):
        """在独立短事务写入已提交的真实 TaskRun 关联。"""
        task = await db.get(TaskRun, task_id)
        if task is None:
            return False
        await record_line(
            db, user_id=int(task.requested_by), **values,
            project_id=task.project_id, drama_project_id=task.drama_project_id,
            shot_id=shot_id if shot_id is not None else task.shot_id, domain=task.domain,
        )
        return True

    for attempt in range(2):
        written = False
        rollback_confirmed = False
        cleanup_confirmed = False
        try:
            write_error = None
            async with AsyncSessionLocal() as db:
                try:
                    recorded = await write(db)
                    written = recorded
                    await db.commit()
                except Exception as exc:
                    write_error = exc
                    if not written:
                        await db.rollback()
                        rollback_confirmed = True
            # 独立会话退出也成功后，才确认原写入已完整撤回并可重试。
            cleanup_confirmed = True
            if write_error is not None:
                raise write_error
            return recorded
        except Exception:  # noqa: BLE001 — 合成已经成功，记账异常不能再花一次上游费用
            logger.exception("TTS 用量写入失败 task=%s provider=%s model=%s attempt=%s", task_id, provider, model, attempt + 1)
            if written or not (rollback_confirmed and cleanup_confirmed):
                # 提交、回滚或关闭确认不明时，均不能冒险插第二行。
                break
    if not get_settings().billing_enabled:
        return False
    # 不再次依赖网络/DB准备价目；沿用原估量与同一价格函数，保存已有成本供人工核对。
    total = values["tokens"] or (usage["prompt_tokens"] + usage["completion_tokens"])
    if total <= 0 and billing_key == "tts":
        total = get_settings().billing_est_tts_tokens
    cost, charge, _ = charge_fen_for_usage(total, billing_key, raw_usage=fields, model=model)
    raise TTSBillingPending({
        "provider": provider, "model": model[:200], "billing_key": billing_key,
        "cost_fen": cost, "charge_fen": charge,
        "total_tokens": total, "raw_usage": fields,
    })
