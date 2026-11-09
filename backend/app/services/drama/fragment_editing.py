"""分镜保存时区分用户设置与服务端写入的成片运行态。"""
from __future__ import annotations

# 只有服务端写这些键：成片版本、尾帧、生成状态和成片规格。旧页面保存时不能覆盖。
SERVER_FRAGMENT_PARAMS = frozenset({
    "video_versions", "lastFrameUrl", "last_frame_url", "generation", "generation_attempts",
    "aspect_ratio", "resolution", "video_width", "video_height", "video_duration_sec",
})


def merge_saved_fragment_params(current: dict | None, submitted: dict | None) -> dict:
    """用户设置以提交内容为准（可删除），服务端字段一律保留库里的值。"""
    kept = {key: value for key, value in (current or {}).items() if key in SERVER_FRAGMENT_PARAMS}
    edited = {key: value for key, value in (submitted or {}).items() if key not in SERVER_FRAGMENT_PARAMS}
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
