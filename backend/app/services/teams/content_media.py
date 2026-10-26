"""团队媒体副本：只复制已鉴权项目的本地文件，绝不请求任意外部 URL。"""
from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from sqlalchemy import select

from app.models_drama import DramaProject
from app.services import oss, storage
from app.config import get_settings

# 暂态、画布实例和强关联 id 不进入独立资产副本；来源标记单独由服务生成。
DROP_COPY_KEYS = frozenset({"generation", "generation_attempts", "canvas", "canvas_node_id", "project_id", "projectId", "asset_id", "assetId", "asset_ids", "assetIds", "reference_asset_ids", "referenceAssetIds", "character_ids", "sourceAssetId", "sourceCharacterAssetId", "characterAssetId", "episode_id", "episode_ids", "fragment_id", "fragment_ids", "derive_id", "task_id", "task_run_id", "copy_origin", "_team_edit_session_id", "_team_edit_resource"})


class MediaCopier:
    """记录本次新建文件以便失败清理，不触碰来源文件。"""

    def __init__(self, target_id: int, allowed_ids: set[int], *, transfer: bool = False):
        """迁入保持本项目目录不变；独立资产复制总是生成新文件名。"""
        self.target_id, self.allowed_ids, self.transfer = target_id, allowed_ids, transfer
        self.created: list[Path] = []
        self.cache: dict[Path, str] = {}

    def cleanup(self) -> None:
        """仅移除本次创建且仍位于目标项目目录中的具体文件。"""
        root = (storage.GENERATED_ROOT / f"p{self.target_id}").resolve()
        for path in self.created:
            if path.resolve().is_relative_to(root):
                path.unlink(missing_ok=True)

    async def rewrite(self, value, *, clean: bool = False):
        """递归改写媒体 URL；对象中的扩展字段和历史图片版本保持独立内容。"""
        if isinstance(value, dict):
            return {key: await self.rewrite(item, clean=clean) for key, item in value.items() if not clean or key not in DROP_COPY_KEYS}
        if isinstance(value, list):
            return [await self.rewrite(item, clean=clean) for item in value]
        if not isinstance(value, str) or not value.startswith(("/static/", "http://", "https://", "file:")):
            return value
        try:
            parts = urlsplit(value)
        except ValueError as exc:
            raise HTTPException(409, "媒体地址无效，无法复制") from exc
        path = unquote(parts.path)
        base = str(get_settings().public_base_url).rstrip("/")
        local = not parts.netloc or value.startswith(base + "/static/")
        controlled = oss.oss_enabled() and value.startswith(oss.public_base().rstrip("/") + "/")
        if controlled:
            marker = f"/{oss.folder_prefix()}/"
            path = "/static/" + path.split(marker, 1)[1] if marker in path else ""
        if (local or controlled) and path.startswith("/static/generated/"):
            candidate = (storage.STATIC_ROOT / path.removeprefix("/static/")).resolve()
            root = storage.GENERATED_ROOT.resolve()
            if not candidate.is_relative_to(root):
                raise HTTPException(409, "媒体文件路径无效，无法复制")
            relative = candidate.relative_to(root)
            match = re.fullmatch(r"p(\d+)", relative.parts[0]) if relative.parts else None
            if not match or int(match[1]) not in self.allowed_ids:
                raise HTTPException(409, "媒体引用了无权访问的项目文件")
            if self.transfer and int(match[1]) == self.target_id:
                return value
            if candidate.is_file():
                if candidate not in self.cache:
                    dest = storage.project_dir(self.target_id) / f"copy_{uuid.uuid4().hex}{candidate.suffix}"
                    if not dest.resolve().is_relative_to(root):
                        raise HTTPException(409, "目标媒体目录无效，无法复制")
                    self.created.append(dest)
                    try:
                        await asyncio.to_thread(shutil.copy2, candidate, dest)
                    except OSError as exc:
                        raise HTTPException(409, "媒体文件暂时无法复制，请稍后重试") from exc
                    self.cache[candidate] = "/static/" + dest.relative_to(storage.STATIC_ROOT).as_posix()
                return self.cache[candidate]
            if not controlled:
                raise HTTPException(409, "本地媒体文件已不存在，无法复制")
        # 已知 OSS 对象是稳定引用；现有项目/资产删除仅删数据库行，不删除对象。
        if controlled or (local and path.startswith("/static/templates/")) or (self.transfer and parts.scheme in {"http", "https"}):
            return value
        raise HTTPException(409, "请先将外部媒体保存为项目素材，再复制到其他项目")


async def copier_for(db, project: DramaProject, *, transfer: bool = False) -> MediaCopier:
    """由数据库真实归属推导可访问的源项目集合，不信任客户端来源标记。"""
    condition = (DramaProject.user_id == project.user_id) & DramaProject.team_id.is_(None) if transfer or project.team_id is None else DramaProject.team_id == project.team_id
    allowed = set((await db.execute(select(DramaProject.id).where(condition))).scalars().all())
    return MediaCopier(project.id, allowed, transfer=transfer)
