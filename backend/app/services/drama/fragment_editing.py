"""分镜编辑与后台回填共用的短事务锁边界；保存时区分用户设置与服务端写入的成片运行态。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import JSON, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import attributes
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaEpisode, DramaEpisodeFragment

# 只有服务端写这些键：成片版本、尾帧、生成状态、成片规格与参考快照。旧页面保存时不能覆盖。
SERVER_FRAGMENT_PARAMS = frozenset({
    "video_versions", "lastFrameUrl", "last_frame_url", "generation", "generation_attempts",
    "aspect_ratio", "resolution", "video_width", "video_height", "video_duration_sec",
    "reference_snapshot", "video_input_snapshot",
})
EDIT_CONFLICT = "内容已在别处更新，请刷新"


def editable_fragment_params(params: dict | None) -> dict:
    """移除客户端不能注入或覆盖的服务端生成字段。"""
    return {key: value for key, value in (params or {}).items() if key not in SERVER_FRAGMENT_PARAMS}


def merge_saved_fragment_params(current: dict | None, submitted: dict | None) -> dict:
    """用户设置以提交内容为准（可删除），服务端字段一律保留库里的值。"""
    kept = {key: value for key, value in (current or {}).items() if key in SERVER_FRAGMENT_PARAMS}
    edited = editable_fragment_params(submitted)
    return {**edited, **kept}


# 分镜规划进度和来源指纹只由任务写入；fragment_script_changed 是列表响应的临时计算值，不落库。
SERVER_EPISODE_PARAMS = frozenset({
    "fragment_plan_status", "fragment_plan_error", "fragment_plan_mode", "fragment_plan_task_id",
    "fragment_plan_count", "fragment_plan_preserved", "fragment_plan_auto", "fragment_plan_skill_ids",
    "fragment_source_fp",
})


def merge_saved_episode_params(current: dict | None, submitted: dict | None) -> dict:
    """页面整包写回分集设置时，服务端字段一律保留库里的值，旧快照不能改回去。"""
    kept = {key: value for key, value in (current or {}).items() if key in SERVER_EPISODE_PARAMS}
    edited = {
        key: value for key, value in (submitted or {}).items()
        if key not in SERVER_EPISODE_PARAMS and key != "fragment_script_changed"
    }
    return {**edited, **kept}


async def claim_episode_revision(db: AsyncSession, episode_id: int, expected: int) -> int:
    """同一事务原子占有版本与分集行锁，禁止读后比较导致双写。"""
    revision = await db.scalar(
        update(DramaEpisode)
        .where(DramaEpisode.id == episode_id, DramaEpisode.revision == expected)
        .values(revision=DramaEpisode.revision + 1)
        .returning(DramaEpisode.revision)
        .execution_options(synchronize_session=False)
    )
    if revision is None:
        raise HTTPException(status_code=409, detail=EDIT_CONFLICT)
    return int(revision)


async def lock_fragment_for_update(db: AsyncSession, fragment: DramaEpisodeFragment) -> None:
    """外部等待结束后锁分集并刷新分镜，随后合并写入；调用方须及时提交。"""
    with db.no_autoflush:
        episode = await db.scalar(
            select(DramaEpisode.id).where(DramaEpisode.id == fragment.episode_id).with_for_update()
        )
        row = await db.scalar(
            select(DramaEpisodeFragment)
            .where(DramaEpisodeFragment.id == fragment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if episode is None or row is None:
        raise HTTPException(status_code=409, detail=EDIT_CONFLICT)


async def patch_fragment_params(
    db: AsyncSession, fragment: DramaEpisodeFragment, patch: dict, *, remove: tuple[str, ...] = (),
) -> None:
    """公共任务收敛仅修改指定 JSON 字段，等待行锁后基于数据库当前值合并。"""
    # 只提交明确 patch；清除此属性的旧整列 dirty 标记，保留其它任务对象正常 autoflush 的锁序。
    attributes.set_committed_value(fragment, "params", fragment.params)
    params = func.coalesce(cast(DramaEpisodeFragment.params, JSONB), cast({}, JSONB))
    for key in remove:
        params = params.op("-")(key)
    params = params.op("||")(cast(patch, JSONB))
    current = await db.scalar(
        update(DramaEpisodeFragment)
        .where(DramaEpisodeFragment.id == fragment.id)
        .values(params=cast(params, JSON))
        .returning(DramaEpisodeFragment.params)
        .execution_options(synchronize_session=False)
    )
    if current is not None:
        attributes.set_committed_value(fragment, "params", current)
