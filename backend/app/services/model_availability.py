"""Shared model switches for new requests; immutable task routes are unaffected."""

from collections.abc import Sequence

from app.schemas_routing import LogicalModel, LogicalModelCapability


def canonical_model_key(capability: LogicalModelCapability, model_id: str | None) -> str:
    # Imports stay local: presets and strategy registration also use routing helpers.
    from app.services.media_model_presets import resolve_preset_alias

    value = resolve_preset_alias(model_id)
    if capability == "audio" and value:
        from app.services.tokenfree_audio import resolve_tokenfree_tts_model, tokenfree_tts_chat_model

        value = tokenfree_tts_chat_model(resolve_tokenfree_tts_model(value))
    if capability in {"image", "video"} and value:
        from app.services.media_models import get_strategy

        try:
            strategy = get_strategy(value)
            if strategy.capability == capability:
                value = strategy.model_id
        except ValueError:
            pass
    return "".join(value.split()).lower()


def has_managed_models(
    capability: LogicalModelCapability, logical_models: Sequence[LogicalModel],
) -> bool:
    return any(model.capability == capability for model in logical_models)


def _matching_models(
    capability: LogicalModelCapability, model_id: str | None,
    logical_models: Sequence[LogicalModel],
) -> list[LogicalModel]:
    key = canonical_model_key(capability, model_id)
    if not key:
        return []
    models = [model for model in logical_models if model.capability == capability]
    direct = [model for model in models if canonical_model_key(capability, model.id) == key]
    if direct:
        return direct
    return [model for model in models if any(
        canonical_model_key(capability, binding.upstream_model) == key for binding in model.bindings
    )]


def is_managed_model(
    capability: LogicalModelCapability, model_id: str | None,
    logical_models: Sequence[LogicalModel],
) -> bool:
    return bool(_matching_models(capability, model_id, logical_models))


def is_model_enabled(
    capability: LogicalModelCapability, model_id: str | None,
    logical_models: Sequence[LogicalModel],
) -> bool:
    """Keep unmanaged custom names compatible; explicit switches cover their aliases."""
    matches = _matching_models(capability, model_id, logical_models)
    return not matches or all(model.enabled for model in matches)


def direct_binding_enabled(
    capability: LogicalModelCapability, model_id: str, channel_id: str,
    logical_models: Sequence[LogicalModel],
) -> bool:
    """A raw upstream request must not bypass a managed model's channel bindings."""
    matches = _matching_models(capability, model_id, logical_models)
    if not matches:
        return True
    key = canonical_model_key(capability, model_id)
    return any(model.enabled and binding.enabled and binding.channel_id == channel_id
               and canonical_model_key(capability, binding.upstream_model) == key
               for model in matches for binding in model.bindings)
