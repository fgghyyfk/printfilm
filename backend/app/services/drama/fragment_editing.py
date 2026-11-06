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
