"""把极限词库应用到开口播的业务文案：按项目类型选模式、清洗镜头文案、扫描生成提示。

策略：带货/获客类（模板分类含「获客」「带货」或允许保留店名）→ 写入时自动替换；
科普/漫剧 → 写入不改字，编辑页通过扫描接口提示「可能违规用语」由用户自己改。
"""

from __future__ import annotations

import re
from typing import Any

from app.services.compliance.ad_words import REPLACE, REPORT, Hit, check_text

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
def sanitize_script(script: str, *, mode: str) -> tuple[str, list[Hit]]:
    if not script:
        return script or "", []
    from app.services.seedance_segments import _is_narration_script_line

    hits: list[Hit] = []
    out: list[str] = []
    for line in script.replace("\r\n", "\n").split("\n"):
        if not _is_narration_script_line(line):
            out.append(line)
            continue
        m = SCRIPT_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        body, h = check_text(line[len(prefix):], mode=mode)
        hits += h
        out.append(prefix + body)
    return "\n".join(out), hits


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


# 命中列表转成接口返回结构
def _hit_rows(hits: list[Hit]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    for h in hits:
        seen.setdefault((h.word, h.level), {"word": h.word, "category": h.category, "level": h.level})
    return list(seen.values())


def scan_shots(shots: list[Any], *, title: str = "") -> list[dict]:
    """扫描科普项目各镜头文案（只报告不改），返回 [{scope, shot_id, index, field, hits}]。"""
    items: list[dict] = []
    if title:
        _, h = check_text(title, mode=REPORT)
        if h:
            items.append({"scope": "project", "shot_id": None, "index": None, "field": "title", "hits": _hit_rows(h)})
    for i, shot in enumerate(shots):
        for field in ("narration", "overlay_title", "overlay_subtitle"):
            text = getattr(shot, field, None) or ""
            _, h = check_text(text, mode=REPORT)
            if h:
                items.append({
                    "scope": "shot", "shot_id": getattr(shot, "id", None),
                    "index": getattr(shot, "shot_no", None) or i + 1, "field": field, "hits": _hit_rows(h),
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
    """扫描漫剧分镜台词（只报告），返回 [{fragment_id, index, hits}]。"""
    items: list[dict] = []
    for i, frag in enumerate(fragments):
        _, h = check_text(drama_spoken_text(getattr(frag, "content", "") or ""), mode=REPORT)
        if h:
            items.append({
                "fragment_id": getattr(frag, "id", None),
                "index": i + 1,
                "hits": _hit_rows(h),
            })
    return items
