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


def profile_state(channels: list[SystemModelChannel], legacy_defaults: DefaultModels):
    """构造所选渠道路由；旧 TokenFree 默认模型迁移一次后保留。"""
    from app.services.model_routing_config import synchronize_logical_models_with_channels

    active = active_profile(channels)
    if active is None:
        return [], DefaultModels()
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
            models.append(LogicalModel(id=mid, name=mid, capability=cap, bindings=[
                LogicalModelBinding(id=active.id+':'+cap, channel_id=active.id, upstream_model=mid)
            ]))
    return models, defaults


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
