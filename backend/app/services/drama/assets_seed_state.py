"""资产抽取状态的回收与保护：params 里的 generating 只有对应任务还活着才算数。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaProject
from app.models_tasks import TaskRun
from app.services.tasks.service import ACTIVE_TASK_STATUSES

# 这些任务会抽取资产：独立抽取、确认分集（内联 seed_assets），以及摘要和分集流水线内的补抽。
_SEEDING_TASK_TYPES = ("seed_assets", "script_summary", "episode_script")
# 状态先于任务提交，留出建任务的窗口，避免刚写入就被判定为遗留。
_ADMISSION_GRACE = timedelta(minutes=2)
SEED_INTERRUPTED_MESSAGE = "资产抽取已中断，请重新抽取"


def merge_saved_project_params(current: dict | None, submitted: dict | None) -> dict:
    """页面整包写回项目设置时，assets_seed_* 只保留库里的值，旧快照不能改回去。"""
    kept = {key: value for key, value in (current or {}).items() if key.startswith("assets_seed_")}
    edited = {key: value for key, value in (submitted or {}).items() if not key.startswith("assets_seed_")}
    return {**edited, **kept}


def _within_admission_grace(params: dict) -> bool:
    """判断 generating 是否刚写入；旧数据没有时间或时间无法解析时按已过期处理。"""
    try:
        started = datetime.fromisoformat(str(params.get("assets_seed_generating_at") or ""))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) - started < _ADMISSION_GRACE


async def assets_seed_in_progress(db: AsyncSession, project_id: int, params: dict | None) -> bool:
    """generating 且（刚写入或有活动抽取任务）才算进行中；进程重启遗留的状态不算。"""
    data = params if isinstance(params, dict) else {}
    if str(data.get("assets_seed_status") or "") != "generating":
        return False
    if _within_admission_grace(data):
        return True
    active = await db.scalar(
        select(TaskRun.id)
        .where(
            TaskRun.drama_project_id == project_id,
            TaskRun.domain == "drama",
            TaskRun.task_type.in_(_SEEDING_TASK_TYPES),
            TaskRun.status.in_(tuple(ACTIVE_TASK_STATUSES)),
        )
        .limit(1)
    )
    return active is not None


async def _is_stale_generating(db: AsyncSession, project_id: int, params: dict) -> bool:
    """仍标记 generating，但已无对应任务在跑。"""
    if str(params.get("assets_seed_status") or "") != "generating":
        return False
    return not await assets_seed_in_progress(db, project_id, params)


async def reconcile_stale_assets_seed(db: AsyncSession, project: DramaProject) -> bool:
    """把遗留的 generating 转为 failed，页面才能结束等待并允许重试；返回是否有修改。"""
    if str((project.params or {}).get("assets_seed_status") or "") != "generating":
        return False
    # 锁行重读，判定与写入之间任务完成时不能覆盖它写入的结果。
    await db.refresh(project, attribute_names=["params"], with_for_update=True)
    params = dict(project.params or {})
    if not await _is_stale_generating(db, int(project.id), params):
        # 没有写入，提交只为释放行锁；回滚会让请求内已加载的对象全部过期。
        await db.commit()
        return False
    params["assets_seed_status"] = "failed"
    params["assets_seed_error"] = SEED_INTERRUPTED_MESSAGE
    params.pop("assets_seed_generating_at", None)
    project.params = params
    await db.commit()
    return True
