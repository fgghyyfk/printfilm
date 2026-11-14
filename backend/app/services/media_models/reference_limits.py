"""Canonical per-model reference limits, shared by catalog, admission and execution."""
from app.schemas_routing import MediaReferenceLimits
from . import MediaValidationError, get_strategy, strategies


def reference_limit_settings(overrides: dict | None = None) -> dict[str, dict[str, int]]:
    result = {strategy.model_id: strategy.describe()["reference_limits"] for strategy in strategies()}
    for model_id, limits in (overrides or {}).items():
        strategy = get_strategy(model_id)
        if model_id != strategy.model_id:
            raise MediaValidationError("media_reference_limits", "请使用规范模型 ID 配置参考素材上限")
        values = MediaReferenceLimits.model_validate(limits).model_dump()
        if strategy.capability == "image" and (values["videos"] or values["audios"]):
            raise MediaValidationError("media_reference_limits", f"{model_id} 仅支持参考图片，视频和音频上限须为 0")
        result[model_id] = values
    return result
