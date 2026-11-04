"""把极限词库应用到开口播的业务文案：按项目类型选模式、清洗镜头文案、扫描生成提示。

策略：带货/获客类（模板分类含「获客」「带货」或允许保留店名）→ 生成时自动替换；
用户手改文案只报告，由用户决定是否修改。
科普/漫剧 → 写入不改字，编辑页通过扫描接口提示「可能违规用语」由用户自己改。
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import asdict, replace
from typing import Any

from app.services.compliance.ad_words import REPLACE, REPORT, Hit, check_text, report_hits

# 「商业」在模板库里也用作画面风格标签（品牌极简、杂志拼贴等），不算带货，只提示不改字
AD_CATEGORIES = {"获客", "带货"}
SCRIPT_PREFIX_RE = re.compile(r"^(\s*【[^】]*】\s*)")
# 漫剧分镜里不检查的结构行：时长、资产、场次、出场人物、场景
DRAMA_SKIP_RE = re.compile(r"^\s*(@|###|出场人物[：:]|日\s|夜\s|内\s|外\s)")
SPEAKER_RE = re.compile(r"^(\s*[^：:\n]{1,16}[（(][^）)]*[）)]\s*[：:]\s*|\s*[^：:\n]{1,12}[：:]\s*)")


# 模板是否属于广告/带货类
def is_ad_template(tpl: Any) -> bool:
    if tpl is None:
        return False
    cats = set(getattr(tpl, "category", None) or [])
    if cats & AD_CATEGORIES:
        return True
    cfg = getattr(tpl, "seedream_config", None) or {}
    return bool(isinstance(cfg, dict) and cfg.get("allow_source_names"))


# 项目使用的清洗模式
def mode_for_template(tpl: Any) -> str:
    return REPLACE if is_ad_template(tpl) else REPORT


# 只清洗脚本里的旁白行（保留【…】前缀与其它所有行）
def sanitize_script(script: str, *, mode: str, _changes: list[dict] | None = None) -> tuple[str, list[Hit]]:
    if not script:
        return script or "", []
    from app.services.seedance_segments import _is_narration_script_line

    hits: list[Hit] = []
    out: list[str] = []
    old_offset, new_offset = 0, 0
    for line in script.split("\n"):
        if not _is_narration_script_line(line):
            out.append(line)
            old_offset += len(line) + 1
            new_offset += len(line) + 1
            continue
        m = SCRIPT_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        changes: list[dict] | None = [] if _changes is not None else None
        body, h = check_text(line[len(prefix):], mode=mode, _changes=changes)
        for change in changes or []:
            _changes.append({**change,
                "original_start": change["original_start"] + old_offset + len(prefix),
                "original_end": change["original_end"] + old_offset + len(prefix),
                "start": change["start"] + new_offset + len(prefix),
                "end": change["end"] + new_offset + len(prefix)})
        old_offset += len(line) + 1
        new_offset += len(prefix) + len(body) + 1
        hits += h
        out.append(prefix + body)
    return "\n".join(out).replace("\r\n", "\n"), hits


def sanitize_shot_fields(fields: dict[str, Any], *, mode: str) -> tuple[dict[str, Any], list[Hit]]:
    """清洗一个镜头的可见文案字段（narration / overlay_title / overlay_subtitle / segment_script / highlights）。

    只返回传入的字段；highlights 会去掉清洗后不再出现在旁白里的关键词。
    """
    out = dict(fields)
    hits: list[Hit] = []
    for key in ("narration", "overlay_title", "overlay_subtitle"):
        if isinstance(out.get(key), str):
            out[key], h = check_text(out[key], mode=mode)
            hits += h
    for key in ("segment_script", "video_prompt"):
        if isinstance(out.get(key), str):
            out[key], h = sanitize_script(out[key], mode=mode)
            if key == "segment_script":
                hits += h
    if mode == REPLACE and isinstance(out.get("highlights"), str) and isinstance(out.get("narration"), str):
        kept = [k for k in out["highlights"].split("|") if k and k in out["narration"]]
        out["highlights"] = "|".join(kept)
    return out, hits


def script_bodies(script: str):
    """沿既有旁白行识别，只剥前缀并保留原稿行起点（CRLF 算两个码点）。"""
    from app.services.seedance_segments import _is_narration_script_line

    offset = 0
    for line in (script or "").splitlines(keepends=True):
        if _is_narration_script_line(line):
            prefix = SCRIPT_PREFIX_RE.match(line)
            start = len(prefix.group(1)) if prefix else 0
            yield line[start:], offset + start
        offset += len(line)


def scan_field(text: str, field: str) -> list[Hit]:
    if field not in {"segment_script", "video_prompt"}:
        return report_hits(text)
    return [replace(hit, start=hit.start + offset, end=hit.end + offset)
            for body, offset in script_bodies(text) for hit in report_hits(body)]


def _report_text(text: str, hits: list[Hit]) -> dict:
    return {"text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest(), "hits": [asdict(hit) for hit in hits]}


def scan_shots(shots: list[Any], *, title: str = "", title2: str = "") -> list[dict]:
    """扫描科普项目各镜头文案（只报告不改），返回 [{scope, shot_id, index, field, hits}]。"""
    items: list[dict] = []
    for field, text in (("title", title), ("avatar_title2", title2)):
        h = scan_field(text, field)
        if h:
            items.append({"scope": "project", "shot_id": None, "index": None, "field": field, **_report_text(text, h)})
    for i, shot in enumerate(shots):
        for field in ("narration", "overlay_title", "overlay_subtitle", "highlights", "segment_script", "video_prompt"):
            text = getattr(shot, field, None) or ""
            h = scan_field(text, field)
            if h:
                items.append({
                    "scope": "shot", "shot_id": getattr(shot, "id", None),
                    "index": getattr(shot, "shot_no", None) or i + 1, "field": field, **_report_text(text, h),
                })
    return items


# 取漫剧分镜行里真正会被说出/显示的文字（去掉说话人前缀与结构行）
def drama_spoken_text(content: str) -> str:
    lines: list[str] = []
    for raw in (content or "").replace("\r\n", "\n").split("\n"):
        if not raw.strip() or DRAMA_SKIP_RE.match(raw):
            continue
        line = SCRIPT_PREFIX_RE.sub("", raw)
        line = SPEAKER_RE.sub("", line, count=1)
        lines.append(line)
    return "\n".join(lines)


def scan_fragments(fragments: list[Any]) -> list[dict]:
    """扫描台词时将剥除说话人后的码点位置映回完整原稿，不改变结构行。"""
    items: list[dict] = []
    for i, frag in enumerate(fragments):
        content = getattr(frag, "content", "") or ""
        hits: list[Hit] = []
        offset = 0
        for raw in content.splitlines(keepends=True):
            if raw.strip() and not DRAMA_SKIP_RE.match(raw):
                prefix = SCRIPT_PREFIX_RE.match(raw)
                start = len(prefix.group(1)) if prefix else 0
                speaker = SPEAKER_RE.match(raw[start:])
                start += len(speaker.group(0)) if speaker else 0
                hits.extend(replace(hit, start=hit.start + offset + start, end=hit.end + offset + start)
                            for hit in report_hits(raw[start:]))
            offset += len(raw)
        if hits:
            items.append({"fragment_id": getattr(frag, "id", None), "index": i + 1, **_report_text(content, hits)})
    return items
