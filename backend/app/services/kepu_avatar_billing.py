"""数字人计费：改图 / 写稿 / OpenAI 配音的用量行，以及全自动出片、批量生产的预扣估价。

上游是 promptoll（OpenAI 兼容），按次或按字计价，这里按官方价表折成分写进 usage_events（raw.cost_fen），
由所在 TaskRun 结算；对口型 GPU 按秒计费仍在 kepu_avatar._record_avatar_usage。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.models import Project

logger = logging.getLogger(__name__)

# promptoll 改图单价（美元/张，含网关加价）；未列出的模型按 DEFAULT_IMAGE_USD
IMAGE_USD: dict[str, float] = {
    "gpt-image-2": 0.2625,
    "gemini-3.1-flash-image": 0.07035,
    "gemini-3.1-flash-image-preview": 0.07035,
    "gemini-2.5-flash-image": 0.04095,
}
DEFAULT_IMAGE_USD = 0.08
# OpenAI tts-1-hd：30 美元 / 百万字符
SPEECH_USD_PER_M_CHARS = 30.0
# 口播语速约 4.5 字/秒；只给选题时按 60 秒成片估
CHARS_PER_SEC = 4.5
THEME_EST_SECONDS = 60.0
# 每段约 12 秒（写稿时按 GPU 单段上限切分）
SEGMENT_SEC = 12.0
# 定妆照 / 单段场景重画：单用户每小时上限
IMAGE_HOURLY_LIMIT = 30
IMAGE_TASK_TYPES = ("avatar_look", "avatar_scene")

class ImageRateLimited(ValueError):
    """改图限频（接口返回 429）。"""

    status_code = 429


# 请求内同步计费时绑定的会话：TaskRun 还没提交，用量行要写在同一个会话里
_bound_db: ContextVar[AsyncSession | None] = ContextVar("avatar_billing_db", default=None)
# 进程内正在改图的用户（同一用户同时只允许一张）
_image_busy_users: set[int] = set()


@contextmanager
def bind_session(db: AsyncSession) -> Iterator[None]:
    """在这个范围内，record_avatar_ai 把用量写进 db（调用方负责提交）。"""
    token = _bound_db.set(db)
    try:
        yield
    finally:
        _bound_db.reset(token)


def _usd_to_fen(usd: float, settings: Settings) -> int:
    """美元折人民币分（向上取整，至少 1 分）。"""
    rate = float(settings.billing_usd_cny or 7.2)
    return max(1, math.ceil(usd * rate * 100))


def image_cost_fen(model: str | None, settings: Settings | None = None) -> int:
    """一张改图的上游成本（分）。"""
    from app.services.avatar_ai import resolve_image_model

    s = settings or get_settings()
    return _usd_to_fen(IMAGE_USD.get(resolve_image_model(model), DEFAULT_IMAGE_USD), s)


def speech_cost_fen(chars: int, settings: Settings | None = None) -> int:
    """OpenAI 配音的上游成本（分）。"""
    s = settings or get_settings()
    return _usd_to_fen(max(1, chars) * SPEECH_USD_PER_M_CHARS / 1_000_000, s)


def volc_tts_cost_fen(chars: int, settings: Settings | None = None) -> int:
    """火山豆包配音的成本（分），按后台每万字符单价。"""
    s = settings or get_settings()
    return max(1, math.ceil(max(1, chars) * int(s.volc_tts_fen_per_10k_chars or 0) / 10_000))


async def record_avatar_ai(billing_key: str, *, model: str, cost_fen: int | None = None,
                           tokens: int = 0, detail: dict | None = None) -> None:
    """在当前 TaskRun 的计费范围内记一笔数字人素材服务用量；不在任务里（无法结算）时只打日志。

    billing_key：avatar_image（按张）/ avatar_tts（按字）/ llm_chat（按 token）。
    """
    from app.models_tasks import TaskRun
    from app.services.billing import record_line
    from app.services.billing.context import get_current_task_run_id

    tid = get_current_task_run_id()
    if tid is None:
        logger.warning("avatar_ai usage outside task scope key=%s model=%s", billing_key, model)
        return
    raw = {"cost_fen": int(cost_fen), **(detail or {})} if cost_fen is not None else None
    bound = _bound_db.get()
    if bound is not None:
        task = await bound.get(TaskRun, tid)
        if task:
            await record_line(bound, user_id=int(task.requested_by), billing_key=billing_key, model=model,
                              tokens=int(tokens or 0), raw=raw, provider="avatar_ai",
                              project_id=task.project_id, shot_id=task.shot_id, domain=task.domain)
        return
    try:
        async with AsyncSessionLocal() as db:
            task = await db.get(TaskRun, tid)
            if not task:
                return
            await record_line(db, user_id=int(task.requested_by), billing_key=billing_key, model=model,
                              tokens=int(tokens or 0), raw=raw, provider="avatar_ai",
                              project_id=task.project_id, shot_id=task.shot_id, domain=task.domain)
            await db.commit()
    except Exception:  # noqa: BLE001 — 记账失败不影响出片，事后可按日志补
        logger.exception("avatar_ai usage record failed task=%s key=%s", tid, billing_key)


def _script_chars(project: Project) -> int:
    """已有分镜取口播总字数，否则按原文估（选题按 60 秒）。"""
    shots = list(project.shots or []) if "shots" in project.__dict__ else []
    text = "".join((s.narration or "") for s in shots)
    if text:
        return len(text)
    if (project.source_type or "") == "script":
        return len(project.source_text or "")
    return int(THEME_EST_SECONDS * CHARS_PER_SEC)


def scene_image_count(project: Project, segments: int) -> int:
    """出片需要画几张场景图：保持原样 + 全片固定不画；全片固定画 1 张；每段换场景每段 1 张。"""
    from app.services.kepu_avatar_prompts import ORIGINAL_LOOK

    if (project.avatar_scene_mode or "per_segment") == "fixed":
        return 0 if (project.avatar_look_style or "") == ORIGINAL_LOOK else 1
    return segments


def estimate_auto_fen(project: Project, settings: Settings | None = None, *, chars: int | None = None) -> int:
    """全自动出片的预扣（分，含加价）：写稿 + 场景图 + 配音 + 对口型 GPU。

    chars 不给时按项目现有分镜/原文估；批量估价时按每条选题的原文传入。
    """
    from app.services.billing.pricing import user_charge_fen
    from app.services.tokenfree_pricing import charge_fen_official_llm
    from app.services.voices import openai_voice

    s = settings or get_settings()
    n_chars = chars if chars is not None else _script_chars(project)
    seconds = max(5.0, n_chars / CHARS_PER_SEC)
    segments = max(1, math.ceil(seconds / SEGMENT_SEC))
    cost = scene_image_count(project, segments) * image_cost_fen(project.avatar_image_model, s)
    from app.services import volc_tts

    if openai_voice(project.voice_id or ""):
        cost += speech_cost_fen(n_chars, s)
    elif volc_tts.is_configured() and volc_tts.is_volc_speaker(project.voice_id or ""):
        cost += volc_tts_cost_fen(n_chars, s)
    total = user_charge_fen(cost, s) + charge_fen_official_llm(s.billing_est_llm_tokens, s)
    total += math.ceil(seconds * int(s.avatar_price_fen_per_sec or 0))
    buf = float(s.billing_estimate_buffer or 1.2)
    return max(1, math.ceil(total * buf))


async def ensure_batch_balance(db: AsyncSession, user, src: Project, topics: list[str], source_type: str) -> int:
    """批量提交前一次性校验余额（含排队中未冻结的任务），不足抛 ValueError；返回本批预计费用（分）。"""
    from app.services.billing.settlement import billing_active, pending_task_commitment_fen

    need = estimate_batch_fen(src, topics, source_type)
    if not billing_active(user):
        return need
    pending = await pending_task_commitment_fen(db, int(user.id))
    available = int(user.balance_fen or 0)
    if available < need + pending:
        extra = f"（另有排队中 ¥{pending / 100:.2f}）" if pending else ""
        raise ValueError(f"余额不足：批量生产 {len(topics)} 条预计 ¥{need / 100:.2f}{extra}，"
                         f"当前 ¥{available / 100:.2f}，请先充值")
    return need


def estimate_batch_fen(src: Project, topics: list[str], source_type: str) -> int:
    """批量生产的总预扣：逐条按原文长度估（选题按 60 秒）。"""
    total = 0
    for topic in topics:
        chars = len(topic) if source_type == "script" else int(THEME_EST_SECONDS * CHARS_PER_SEC)
        total += estimate_auto_fen(src, chars=chars)
    return total


def estimate_scene_images_fen(project: Project, settings: Settings | None = None) -> int:
    """素材阶段还要画的场景图费用（分，含加价）。"""
    from app.services.billing.pricing import user_charge_fen
    from app.services.kepu_avatar_look import needs_scene

    s = settings or get_settings()
    shots = list(project.shots or [])
    pending = [sh for sh in shots if needs_scene(sh, project.avatar_image_url)]
    if not pending:
        return 0
    count = 1 if (project.avatar_scene_mode or "") == "fixed" else len(pending)
    return user_charge_fen(count * image_cost_fen(project.avatar_image_model, s), s)


async def check_image_quota(db: AsyncSession, user_id: int) -> None:
    """改图限频：同一用户同时只能一张，每小时最多 IMAGE_HOURLY_LIMIT 张；超出抛 ValueError（中文）。"""
    from app.models_tasks import TaskRun

    if user_id in _image_busy_users:
        raise ImageRateLimited("上一张图还在生成，请稍等")
    since = datetime.now(UTC) - timedelta(hours=1)
    count = await db.scalar(select(func.count()).select_from(TaskRun).where(
        TaskRun.requested_by == user_id, TaskRun.domain == "kepu",
        TaskRun.task_type.in_(IMAGE_TASK_TYPES), TaskRun.created_at >= since))
    if int(count or 0) >= IMAGE_HOURLY_LIMIT:
        raise ImageRateLimited(f"生成太频繁：每小时最多 {IMAGE_HOURLY_LIMIT} 张，请稍后再试")


async def run_billed_image(db: AsyncSession, user, *, task_type: str, project_id: int, model: str,
                           shot_id: int | None = None, executor) -> None:
    """定妆照 / 场景重画：限频 → 建轻量任务预扣（按所选模型的张价）→ 执行 → 按实际用量结算。

    余额不足、限频抛 ValueError；执行失败原样抛出（冻结额全退）。
    """
    from app.services.billing.ephemeral import run_billed_ephemeral

    await check_image_quota(db, int(user.id))
    _image_busy_users.add(int(user.id))
    try:
        async def _run():
            with bind_session(db):
                return await executor()

        await run_billed_ephemeral(db, user, domain="kepu", task_type=task_type, executor=_run,
                                   payload={"model": model, "project_id": project_id},
                                   project_id=project_id, shot_id=shot_id)
    finally:
        _image_busy_users.discard(int(user.id))
