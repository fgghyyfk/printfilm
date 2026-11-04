"""生成文案的窄审计记录：原稿、码点差异及安全单条撤销。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.compliance.ad_words import REPLACE, check_text
from app.services.compliance.content import sanitize_shot_fields


# 从真实清洗步骤获取完整替换词，再纳入字段层额外的关键词/换行清理。
def _text_changes(original: str, result: str, *, script: bool = False) -> list[dict]:
    from app.services.compliance._tracked import TrackedText
    from app.services.compliance.content import sanitize_script

    changes: list[dict] = []
    if script:
        sanitize_script(original, mode=REPLACE, _changes=changes)
    else:
        check_text(original, _changes=changes)
    trace = TrackedText(original)
    for change in reversed(changes):
        trace.replace(change["original_start"], change["original_end"], change["new"])
    trace.rewrite(result)
    return trace.changes()


# 清洗本次生成的可见字段，只保存确实改变的原稿与差异，不保存整份模型响应。
def clean_fields(fields: dict[str, Any]) -> tuple[dict[str, Any], dict]:
    cleaned, _ = sanitize_shot_fields(fields, mode=REPLACE)
    for field in ("title", "avatar_title2"):
        if isinstance(fields.get(field), str):
            cleaned[field] = check_text(fields[field], mode=REPLACE)[0]
    audit: dict = {}
    for field, original in fields.items():
        result = cleaned.get(field)
        if isinstance(original, str) and isinstance(result, str) and original != result:
            audit[field] = {"original": original, "result": result, "changes": _text_changes(original, result, script=field in {"segment_script", "video_prompt"})}
    return cleaned, audit


# 按原稿区间重建尚未撤销的替换，同时计算当前码点位置（撤销长度变化不会错位）。
def render_record(record: dict) -> tuple[str, list[dict]]:
    original = record["original"]
    chunks: list[str] = []
    rows: list[dict] = []
    cursor, length = 0, 0
    for change in record["changes"]:
        prefix = original[cursor:change["original_start"]]
        chunks.append(prefix)
        length += len(prefix)
        value = change["old"] if change.get("undone") else change["new"]
        rows.append({**change, "current_start": length, "current_end": length + len(value)})
        chunks.append(value)
        length += len(value)
        cursor = change["original_end"]
    chunks.append(original[cursor:])
    return "".join(chunks), rows


# 仅当当前文本仍等于审计记录时撤销，拒绝用陈旧位置覆盖用户后来修改的文字。
def undo_change(fields: dict[str, Any], audit: dict, field: str, change_id: str) -> tuple[dict, dict]:
    record = audit.get(field)
    if not record:
        raise ValueError("替换记录不存在")
    expected, _ = render_record(record)
    if fields.get(field) != expected:
        raise ValueError("文案已编辑，无法按旧位置撤销；请对照原稿手动修改")
    updated = deepcopy(audit)
    target = next((c for c in updated[field]["changes"] if c["id"] == change_id), None)
    if not target or target.get("undone"):
        raise ValueError("替换记录不存在或已撤销")
    target["undone"] = True
    text, _ = render_record(updated[field])
    return {**fields, field: text}, updated


# 真实生成历史独立于当前风险扫描返回；手改后仍可复核原稿，但禁止盲撤。
def change_rows(owner: Any, *, scope: str, index: int | None = None) -> list[dict]:
    rows: list[dict] = []
    for field, record in (getattr(owner, "compliance_changes", None) or {}).items():
        expected, changes = render_record(record)
        current = getattr(owner, field, "") or ""
        rows.append({
            "scope": scope, "shot_id": getattr(owner, "id", None) if scope == "shot" else None,
            "index": index, "field": field, "original": record["original"], "text": current,
            "matches_current": expected == current, "changes": changes,
        })
    return rows


# 把脚本中的实际口播码点映射回脚本原稿；一镜多段时仍保留每段边界。
def _spoken_positions(script: str) -> tuple[str, list[int]]:
    import re
    from app.services.seedance_segments import _is_narration_script_line

    spoken: list[str] = []
    positions: list[int] = []
    offset = 0
    for line in script.splitlines(keepends=True):
        if _is_narration_script_line(line):
            body = re.sub(r"^【[^】]*】", "", line.strip()).strip()
            start = line.find(body) if body else 0
            spoken.append(body)
            positions.extend(range(offset + start, offset + start + len(body)))
        offset += len(line)
    return "".join(spoken), positions


# 在旁白原稿与脚本原稿之间定位同一词；真实口播映射优先，避免误认画面里的同文。
def _related_start(source_field: str, field: str, source: str, original: str, target: dict) -> int | None:
    if source_field == "narration":
        spoken, positions = _spoken_positions(original)
        if spoken == source and target["original_end"] <= len(positions):
            return positions[target["original_start"]]
    elif field == "narration":
        spoken, positions = _spoken_positions(source)
        if spoken == original and target["original_start"] in positions:
            return positions.index(target["original_start"])
    offset = original.find(source)
    if offset >= 0:
        return target["original_start"] + offset
    offset = source.find(original)
    if offset >= 0:
        return target["original_start"] - offset
    return None


# 同步同一旁白在脚本/视频提示词内的审计撤销状态，不改变无关画面或制作标记。
def _sync_related(owner: Any, data: dict, audit: dict, source_field: str, target: dict) -> None:
    from app.services.seedance_segments import narration_from_script

    if source_field not in {"narration", "segment_script", "video_prompt"}:
        return
    source = audit[source_field]
    for field in ("narration", "segment_script", "video_prompt"):
        if field == source_field:
            continue
        record = audit.get(field)
        if record:
            expected, _ = render_record(record)
            if expected != (getattr(owner, field, "") or ""):
                raise ValueError("关联旁白或脚本已编辑，请对照原稿手动修改")
            start = _related_start(source_field, field, source["original"], record["original"], target)
            match = next((c for c in record["changes"] if c["original_start"] == start and c["old"] == target["old"] and c["new"] == target["new"]), None)
            # 整镜与逐行清理可能删除不同的重复标点；用真实口播结果确认等效撤销。
            if not match and (source_field == "narration" or field == "narration"):
                spoken = data["narration"] if source_field == "narration" else narration_from_script(data[source_field])
                for candidate in record["changes"]:
                    if candidate.get("undone") or candidate["old"] != target["old"] or candidate["new"] != target["new"]:
                        continue
                    candidate["undone"] = True
                    text = render_record(record)[0]
                    candidate["undone"] = False
                    if (text if field == "narration" else narration_from_script(text)) == spoken:
                        match = candidate
                        break
            if match:
                match["undone"] = True
                data[field] = render_record(record)[0]
                continue
            raise ValueError("无法确定关联口播的替换位置，请对照原稿手动修改")
        if source_field == "narration" and getattr(owner, field, ""):
            raise ValueError("无法确定关联口播的替换位置，请对照原稿手动修改")
        elif source_field in {"segment_script", "video_prompt"}:
            previous = narration_from_script(getattr(owner, source_field)) if field == "narration" else getattr(owner, source_field)
            if (getattr(owner, field, "") or "") != previous:
                raise ValueError("关联旁白或脚本已编辑，请对照原稿手动修改")
            data[field] = narration_from_script(data[source_field]) if field == "narration" else data[source_field]


# 从PATCH请求取出窄撤销指令，不接受同时编辑以免覆盖经过校验的撤销文本。
def prepare_undo(owner: Any, data: dict, *, allowed: set[str]) -> tuple[dict, bool]:
    request = data.pop("compliance_undo", None)
    if not request:
        return data, False
    field = request["field"]
    if data or field not in allowed:
        raise ValueError("撤销请求只能指定已有替换记录")
    fields = {key: getattr(owner, key, "") or "" for key in allowed}
    updated, audit = undo_change(fields, getattr(owner, "compliance_changes", None) or {}, field, request["change_id"])
    target = next(c for c in audit[field]["changes"] if c["id"] == request["change_id"])
    result = {field: updated[field]}
    _sync_related(owner, result, audit, field, target)
    owner.compliance_changes = audit
    return result, True
