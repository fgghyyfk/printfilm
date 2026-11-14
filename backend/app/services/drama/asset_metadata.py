"""资产当前媒体的来源与参数快照，独立于可编辑的下次生成选项。"""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any


def generated_media_metadata(*, model_id: str | None, prompt: str, options: dict, **extra: Any) -> dict:
    """在成功生成后保存调用参数，不读取之后可能改变的界面默认值。"""
    return {
        "source": "generated", "model_id": model_id or None,
        "prompt": prompt, "parameters": deepcopy(options),
        "created_at": datetime.now(UTC).isoformat(), **extra,
    }


def uploaded_media_metadata(*, filename: str | None, size: int, content_type: str) -> dict:
    """上传替换旧媒体时重新建立快照，避免保留旧模型与提示词。"""
    return {
        "source": "upload", "filename": filename or "", "file_size": size,
        "content_type": content_type, "created_at": datetime.now(UTC).isoformat(),
    }


def current_media_metadata(params: dict | None) -> dict:
    """优先使用成功媒体快照；兼容旧数据，有证据才标注生成或上传。"""
    params = params if isinstance(params, dict) else {}
    snapshot = params.get("media_metadata")
    if isinstance(snapshot, dict):
        return deepcopy(snapshot)
    gen = params.get("generation") if isinstance(params.get("generation"), dict) else {}
    if gen.get("source") == "upload":
        return {"source": "upload"}
    options = params.get("videoOptions") if isinstance(params.get("videoOptions"), dict) else {}
    model = gen.get("model_id") or params.get("model_id") or options.get("model_id")
    if not model:
        return {"source": "unknown"}
    values = {**params, **options, **gen}
    return {
        "source": "generated", "model_id": model,
        "prompt": params.get("visualPrompt") or params.get("voicePrompt") or params.get("prompt") or "",
        "parameters": {key: deepcopy(values[key]) for key in (
            "aspect_ratio", "resolution", "size", "duration_sec", "image_style_id",
            "generate_audio", "speaker", "sampleText",
        ) if values.get(key) is not None},
        "created_at": gen.get("finished_at"),
    }
