"""按集原子更新 JSON，不在模型调用期间持有数据库写锁。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_drama import DramaScript


async def locked_script(db: AsyncSession, project_id: int) -> DramaScript:
    """从最新数据库版本读改写，避免不同集互相覆盖。"""
    row = (await db.execute(select(DramaScript).where(DramaScript.project_id == project_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="剧本不存在")
    return row


def episode_rows(content: dict | list | None) -> list[dict]:
    """兼容旧数组与新 episodes 容器，保留未知扩展字段。"""
    rows = content.get("episodes", []) if isinstance(content, dict) else content
    return [dict(item) for item in (rows or []) if isinstance(item, dict)]


async def patch_episode_body(db: AsyncSession, project_id: int, episode_number: int, patch: dict) -> dict:
    """仅更新明确提交的字段，空字符串表示清空，其他集和扩展数据保持原样。"""
    script = await locked_script(db, project_id)
    rows = episode_rows(script.episode_content)
    item = next((row for row in rows if int(row.get("episodeNumber") or 0) == episode_number), None)
    if item is None:
        raise HTTPException(status_code=404, detail="分集剧本不存在")
    params = dict(script.params or {})
    versions = dict(params.get("episode_versions") or {})
    version = int(versions.get(str(episode_number), 0))
    if patch.get("version") is not None and int(patch["version"]) != version:
        raise HTTPException(status_code=409, detail="本集内容已更新，请刷新后再保存")
    item.update({key: value for key, value in patch.items() if key in {"title", "creative", "summary", "body"} and value is not None})
    container = dict(script.episode_content) if isinstance(script.episode_content, dict) else {}
    container["episodes"] = rows
    script.episode_content = container
    versions[str(episode_number)] = version + 1
    params["episode_versions"] = versions
    script.params = params
    await db.flush()
    return {**item, "version": version + 1}


def set_episode_job_state(script: DramaScript, episode_number: int, status: str, **extra) -> None:
    """单集生成状态分别保存，不再用单一全剧槽位阻止其他集并行。"""
    params = dict(script.params or {})
    jobs = dict(params.get("episode_jobs") or {})
    jobs[str(episode_number)] = {"status": status, **extra}
    params["episode_jobs"] = jobs
    script.params = params


def bump_episode_versions(script: DramaScript, numbers: list[int]) -> None:
    """模型写回同样更新内容版本，生成前的旧草稿不能覆盖新正文。"""
    params = dict(script.params or {})
    versions = dict(params.get("episode_versions") or {})
    for number in set(numbers):
        if number > 0:
            versions[str(number)] = int(versions.get(str(number), 0)) + 1
    params["episode_versions"] = versions
    script.params = params
