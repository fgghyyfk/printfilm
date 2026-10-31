"""手动选择的渠道配置：协议白名单、模型与运行时凭据隔离。"""
from __future__ import annotations

from app.schemas_routing import (
    DefaultModels, LogicalModel, LogicalModelBinding, SystemChannelAdvancedConfig,
    SystemModelChannel, SystemModelChannelIn,
)
from app.services.media_model_presets import all_preset_channel_models, clamp_default_to_preset

TOKENFREE_BASE = 'https://www.tokenfree.com/v1'
BYTEPLUS_BASES = {
    'https://ark.ap-southeast.bytepluses.com/api/v3',
    'https://ark.eu-west.bytepluses.com/api/v3',
}
CAPABILITIES = ('text', 'image', 'video', 'audio')


def is_byteplus(base_url: str) -> bool:
    """仅识别官方 ModelArk 区域接口，避免按模糊字符串发送凭据。"""
    return base_url.rstrip('/') in BYTEPLUS_BASES


def normalize_profiles(items: list[SystemModelChannelIn]) -> list[SystemModelChannelIn]:
    """校验整组配置后再允许落库；每个配置对应一把独立 Key。"""
    if sum(item.enabled for item in items) != 1:
        raise ValueError('必须选择且只能选择一个生效配置')
    if len({item.id.strip() for item in items}) != len(items) or any(not item.id.strip() for item in items):
        raise ValueError('配置 ID 不能为空或重复')
    result = []
    for item in items:
        advanced = item.advanced_config or SystemChannelAdvancedConfig()
        advanced = advanced.model_copy(update={
            cap+'_model': getattr(advanced, cap+'_model').strip() for cap in CAPABILITIES
        })
        base = item.base_url.strip().rstrip('/')
        if advanced.provider == 'byteplus':
            if base not in BYTEPLUS_BASES:
                raise ValueError('BytePlus 接口地址必须选择官方区域')
            if advanced.audio_model:
                raise ValueError('BytePlus 暂不支持独立配音模型')
            models = [getattr(advanced, cap+'_model').strip() for cap in CAPABILITIES]
            if len([m for m in models if m]) != len(set(m for m in models if m)):
                raise ValueError('不同能力请使用不同模型或接入点 ID')
            models = [m for m in models if m]
        else:
            if base != TOKENFREE_BASE:
                raise ValueError('TokenFree 接口地址不正确')
            models = all_preset_channel_models()
            advanced = advanced.model_copy(update={
                cap+'_model': clamp_default_to_preset(cap, getattr(advanced, cap+'_model'))
                for cap in CAPABILITIES
            })
        result.append(item.model_copy(update=dict(
            id=item.id.strip(), name=item.name.strip() or item.id.strip(), base_url=base,
            protocol='auto', api_format='openai', models=models, advanced_config=advanced,
        )))
    return result


def active_profile(channels: list[SystemModelChannel]) -> SystemModelChannel | None:
    """只返回手选配置，不从禁用配置寻找替代 Key。"""
    return next((item for item in channels if item.enabled), None)


def profile_state(
    channels: list[SystemModelChannel],
    legacy_defaults: DefaultModels,
    disabled: set[str] | frozenset[str] = frozenset(),
):
    """构造所选渠道路由；disabled 为后台停用的模型 id，停用后不参与路由，默认模型自动换成同类可用模型。"""
    active = active_profile(channels)
    if active is None:
        return [], DefaultModels()
    models, defaults = _active_profile_state(active, legacy_defaults)
    return _apply_disabled(models, defaults, disabled)


def _apply_disabled(models: list[LogicalModel], defaults: DefaultModels, disabled: set[str] | frozenset[str]):
    """标记停用模型；默认值已停用或为空时取同能力第一个可用模型，全部停用则留空。"""
    models = [model.model_copy(update={'enabled': model.id not in disabled}) for model in models]
    update = {}
    for cap in CAPABILITIES:
        current = getattr(defaults, cap+'_model')
        enabled_ids = _preset_ordered([model.id for model in models if model.capability == cap and model.enabled], cap)
        if current in disabled or (not current and enabled_ids):
            update[cap+'_model'] = enabled_ids[0] if enabled_ids else ''
    return models, defaults.model_copy(update=update) if update else defaults


def _preset_ordered(ids: list[str], capability: str) -> list[str]:
    """按站点预设的推荐顺序排列（预设外的模型排在后面），默认模型回退时优先选推荐款。"""
    from app.services.media_model_presets import preset_ids

    order = {mid: index for index, mid in enumerate(preset_ids(capability))}
    return sorted(ids, key=lambda mid: order.get(mid, len(order)))


def _active_profile_state(active: SystemModelChannel, legacy_defaults: DefaultModels):
    """按生效配置生成逻辑模型与默认模型（未考虑停用）。"""
    from app.services.model_routing_config import synchronize_logical_models_with_channels

    advanced = active.advanced_config
    if not is_byteplus(active.base_url):
        raw = advanced.model_dump() if advanced else legacy_defaults.model_dump()
        defaults = DefaultModels(**{
            cap+'_model': clamp_default_to_preset(cap, raw.get(cap+'_model', ''))
            for cap in CAPABILITIES
        })
        return synchronize_logical_models_with_channels([], [active]), defaults
    raw = advanced.model_dump() if advanced else {}
    defaults = DefaultModels(**{cap+'_model': raw.get(cap+'_model', '') for cap in CAPABILITIES})
    models = []
    for cap in CAPABILITIES:
        mid = getattr(defaults, cap+'_model')
        if mid:
            models.append(LogicalModel(id=mid, name=mid, capability=cap, strategy_id=byteplus_strategy_id(cap, mid), bindings=[
                LogicalModelBinding(id=active.id+':'+cap, channel_id=active.id, upstream_model=mid)
            ]))
    return models, defaults


# BytePlus 型号名 → 参数规则（媒体策略）；接入点 ID（ep-xxx）认不出型号时按能力取默认
_BYTEPLUS_FAMILIES = (
    ('seedance-2-0-fast', 'seedance-2.0-fast'),
    ('seedance-2-5', 'seedance-2.5'),
    ('seedance-2-0', 'seedance-2.0'),
    ('seedream-5-0-pro', 'seedream-5.0-pro'),
    ('seedream-5-0-flash', 'seedream-5.0-flash'),
    ('seedream-5-0', 'seedream-5.0'),
)
_BYTEPLUS_DEFAULT_STRATEGY = {'image': 'seedream-5.0', 'video': 'seedance-2.5'}


def byteplus_strategy_id(capability: str, model_id: str) -> str | None:
    """BytePlus 模型对应的媒体参数规则；文字、配音不需要。"""
    if capability not in _BYTEPLUS_DEFAULT_STRATEGY:
        return None
    from app.services.media_models.strategies import MediaValidationError, get_strategy

    try:
        return get_strategy(model_id).model_id
    except MediaValidationError:
        pass
    raw = model_id.lower().replace('.', '-').replace('_', '-')
    for marker, strategy_id in _BYTEPLUS_FAMILIES:
        if marker in raw:
            return strategy_id
    return _BYTEPLUS_DEFAULT_STRATEGY[capability]


def profile_overlay(flat: dict, channels: list[SystemModelChannel], defaults: DefaultModels) -> dict:
    """地址、Key 和能力完整覆盖；空值不得继承上一渠道的秘密或模型。"""
    active = active_profile(channels)
    if not active:
        return flat
    out = dict(flat)
    out.update(openai_base_url=active.base_url, ark_base_url=active.base_url,
               openai_api_key=active.api_key, ark_api_key=active.api_key)
    for cap, setting in [('text', 'model_llm'), ('image', 'model_image'), ('video', 'model_video'), ('audio', 'model_audio')]:
        out[setting] = getattr(defaults, cap+'_model')
    byteplus = is_byteplus(active.base_url)
    out['model_image_45'] = defaults.image_model if byteplus else 'seedream-4-5'
    out['model_video_2'] = defaults.video_model if byteplus else 'seedance-2.0'
    return out
