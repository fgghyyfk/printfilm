"""分镜编辑与后台回填共用的短事务锁边界。"""
from fastapi import HTTPException
from sqlalchemy import JSON, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import attributes
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaEpisode, DramaEpisodeFragment

# 此处只列服务端实际写入的运行态与成片元数据，普通创作设置仍允许用户编辑。
SERVER_FRAGMENT_PARAMS = frozenset({
    'video_versions', 'lastFrameUrl', 'last_frame_url', 'generation',
    'generation_attempts', 'aspect_ratio', 'resolution', 'video_width', 'video_height',
})
EDIT_CONFLICT = '内容已在别处更新，请刷新'


def editable_fragment_params(params: dict | None) -> dict:
    """移除客户端不能注入或覆盖的服务端生成字段。"""
    return {key: value for key, value in (params or {}).items() if key not in SERVER_FRAGMENT_PARAMS}


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
