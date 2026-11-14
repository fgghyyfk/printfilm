"""保守回收共享p目录内的老孤儿文件；默认dry-run，不删除活动项目缓存。"""
from __future__ import annotations

import os
import json
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import inspect, select, tuple_

from app.database import Base


@dataclass
class ReferenceAudit:
    complete: bool = True
    references: dict[Path, set[str]] = field(default_factory=dict)
    protected_projects: set[int] = field(default_factory=set)
    error: str | None = None


def _canonical(value: str, root: Path) -> Path | None:
    """只返回注入root内路径；公网/OSS旧别名亦保守映射，不输出查询签名。"""
    try:
        text = value.strip()
        if text.startswith(str(root) + os.sep) or text == str(root):
            # 本地绝对路径的?、#和百分号是文件名本身，不按URL解释。
            candidate = Path(text)
        else:
            # 先分离真正的query/hash，再解码路径内合法的%3F/%23。
            path = unquote(urlsplit(text).path)
            marker = "/generated/"
            index = path.find(marker)
            if index < 0:
                return None
            candidate = root / path[index + len(marker):]
        resolved = candidate.resolve()
        return resolved if resolved.is_relative_to(root) else None
    except (OSError, ValueError, RuntimeError):
        return None


def _reference_values(value, owner: str, root: Path, audit: ReferenceAudit) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"project_id", "drama_project_id", "projectId", "dramaProjectId"}:
                if isinstance(item, (int, str)) and str(item).isdigit() and int(item) > 0:
                    audit.protected_projects.add(int(item))
            _reference_values(item, owner, root, audit)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reference_values(item, owner, root, audit)
    elif isinstance(value, str):
        if value.lstrip().startswith(("{", "[", '"')):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = value
            if decoded != value:
                _reference_values(decoded, owner, root, audit)
        # 整字段先处理（保留带空格文件名），再识别脚本/正文里的媒体引用。
        tokens = [value, *re.findall(r"(?:https?://|/static/generated/|" + re.escape(str(root)) + r"/)[^\s<>\"'`，。；、)\]}]+", value)]
        for token in tokens:
            # 保留完整带标点文件名，同时保守保护正文边界前缀，宁可多留。
            candidates = [token, token.rstrip(".,;:!?"), *[token[:match.start()] for match in re.finditer(r"[,;!?]", token)]]
            for candidate in candidates:
                path = _canonical(candidate, root)
                if path is not None:
                    audit.references.setdefault(path, set()).add(owner)


async def collect_reference_audit(db, root: Path) -> ReferenceAudit:
    """逐表主键分页扫描所有列/嵌套JSON，包含终态任务及全局配置；不截断。"""
    # 注册全部持久化模型，不以OSS上传白名单代替引用清单。
    from app import (  # noqa: F401
        models, models_agent, models_creation, models_drama, models_ecom, models_settings, models_tasks,
        models_team_billing, models_team_locks, models_teams,
    )

    audit = ReferenceAudit()
    if db.new or db.dirty or db.deleted:
        return ReferenceAudit(complete=False, error="pending_database_changes")
    try:
        connection = await db.connection()
        def inventory(sync):
            inspector = inspect(sync)
            names = set(inspector.get_table_names())
            return names, {name: {c["name"] for c in inspector.get_columns(name)} for name in names}
        names, columns = await connection.run_sync(inventory)
        tables = Base.metadata.tables
        if names - set(tables) - {"alembic_version"} or set(tables) - names:
            return ReferenceAudit(complete=False, error="reference_schema_incomplete")
        for table in sorted(tables.values(), key=lambda item: item.name):
            if columns[table.name] != {c.name for c in table.columns}:
                return ReferenceAudit(complete=False, error="reference_schema_incomplete")
            primary = list(table.primary_key.columns)
            if not primary:
                return ReferenceAudit(complete=False, error="reference_schema_incomplete")
            last = None
            while True:
                statement = select(table).order_by(*primary).limit(200)
                if last is not None:
                    statement = statement.where(primary[0] > last[0] if len(primary) == 1 else tuple_(*primary) > tuple_(*last))
                rows = (await db.execute(statement)).mappings().all()
                for row in rows:
                    ids = [str(row[column.name]) for column in primary]
                    # 拥有者只显示表与记录标识，不回传配置、密钥、URL或整JSON。
                    owner = table.name + ":" + ",".join(re.sub(r"[^\w.-]", "_", value)[:80] for value in ids)
                    if table.name in {"projects", "drama_projects"}:
                        audit.protected_projects.add(int(row["id"]))
                    if table.name == "task_targets" and row.get("target_type") in {"project", "drama_project"}:
                        audit.protected_projects.add(int(row["target_id"]))
                    _reference_values(dict(row), owner, root, audit)
                if len(rows) < 200:
                    break
                last = tuple(rows[-1][column.name] for column in primary)
    except Exception:
        # 任何查询/遍历失败都禁止apply，不把含参数的异常原文打印为报告。
        return ReferenceAudit(complete=False, error="reference_audit_failed")
    return audit


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _owners(path: Path, root: Path, audit: ReferenceAudit) -> set[str]:
    owners: set[str] = set()
    for parent in (path, *path.parents):
        if not parent.is_relative_to(root):
            break
        owners.update(audit.references.get(parent, set()))
    return owners


def _unlink_unchanged(root: Path, relative: Path, expected: tuple, cutoff: float) -> bool:
    """逐级O_NOFOLLOW目录句柄+末次身份核对，路径被换成symlink也不越界。"""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        current = os.stat(relative.name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or _signature(current) != expected or max(current.st_mtime, current.st_ctime) >= cutoff:
            return False
        os.unlink(relative.name, dir_fd=fd)
        return True
    finally:
        os.close(fd)


async def audit_generated_orphans(db, *, root: Path, retention_days: int = 30, apply: bool = False, now: float | None = None) -> dict:
    """仅扫描显式root的顶层pN；其他目录、p0、活动项目和隐式缓存全部保留。"""
    report = {"ok": False, "dry_run": not apply, "candidates": [], "deleted": [], "kept": []}
    if retention_days < 1:
        return {**report, "error": "retention_must_be_positive"}
    if root.is_symlink() or not root.is_dir():
        return {**report, "error": "unsafe_generated_root"}
    root = root.resolve()
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    audit = await collect_reference_audit(db, root)
    if not audit.complete:
        return {**report, "error": audit.error}
    candidates = []
    try:
        for directory in sorted(root.iterdir()):
            match = re.fullmatch(r"p([1-9]\d*)", directory.name)
            if not match or directory.is_symlink() or not directory.is_dir():
                continue
            if int(match[1]) in audit.protected_projects:
                report["kept"].append({"path": str(directory), "reason": "project_or_history_owner"})
                continue
            def walk_error(error):
                raise error
            for parent, directories, files in os.walk(directory, followlinks=False, onerror=walk_error):
                directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
                for name in files:
                    path = Path(parent) / name
                    info = path.lstat()
                    if ".part" in path.suffixes or not stat.S_ISREG(info.st_mode) or max(info.st_mtime, info.st_ctime) >= cutoff:
                        continue
                    owners = _owners(path, root, audit)
                    if owners:
                        report["kept"].append({"path": str(path), "owners": sorted(owners)})
                    else:
                        candidates.append((path, _signature(info)))
        report["candidates"] = [str(path) for path, _ in candidates]
    except OSError:
        return {**report, "error": "generated_scan_incomplete"}
    if apply:
        for path, signature in candidates:
            # 每个文件前重做完整引用审计；审计失败/新增引用/文件变化一律保留。
            fresh = await collect_reference_audit(db, root)
            if not fresh.complete:
                return {**report, "error": fresh.error}
            project_id = int(path.relative_to(root).parts[0][1:])
            if project_id in fresh.protected_projects or _owners(path, root, fresh):
                report["kept"].append({"path": str(path), "reason": "reference_added"})
                continue
            try:
                if _unlink_unchanged(root, path.relative_to(root), signature, cutoff):
                    report["deleted"].append(str(path))
                else:
                    report["kept"].append({"path": str(path), "reason": "file_changed"})
            except OSError:
                report["kept"].append({"path": str(path), "reason": "file_changed"})
    return {**report, "ok": True}
