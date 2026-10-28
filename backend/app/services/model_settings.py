"""Load, cache, and persist model routing + flat runtime settings."""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings, reload_settings
from app.models_settings import AppSettings, SystemModelChannelRow
from app.schemas_routing import (
    AdminRoutingSettingsOut,
    AdminRoutingSettingsPatch,
    DefaultModels,
    LogicalModel,
    LogicalModelCapability,
    SystemChannelAdvancedConfig,
    SystemModelChannel,
    default_models_from_dict,
    default_models_to_dict,
)
from app.schemas_settings import (
    SECRET_FIELD_FLAGS,
    SECRET_FIELDS,
    AdminModelSettingsOut,
    AdminModelSettingsPatch,
    ModelCapabilityReadiness,
    model_config_field_names,
)
from app.services.model_routing_config import (
    model_routing_validation_errors,
    normalize_default_models,
    resolve_logical_model_config,
    synchronize_logical_models_with_channels,
)

logger = logging.getLogger("app.model_settings")

ENCRYPTED_PREFIX = "enc:"

_overlay: dict[str, Any] = {}


@dataclass
class RoutingSnapshot:
    channels: list[SystemModelChannel]
    logical_models: list[LogicalModel]
    default_models: DefaultModels


_routing_snapshot = RoutingSnapshot(channels=[], logical_models=[], default_models=DefaultModels())


# 从 secret_key 派生 Fernet 密钥
def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# 加密敏感字段
def _encrypt_secret(value: str) -> str:
    token = _fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


# 解密敏感字段；密钥轮换/错配时返回空串，避免拖垮启动
def _decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("密文字段无法用当前 SECRET_KEY 解密，已忽略（需在管理端重填）")
        return ""


# 返回当前路由快照
def get_routing_snapshot() -> RoutingSnapshot:
    return _routing_snapshot


# 返回 flat overlay
def get_overlay_dict() -> dict[str, Any]:
    return dict(_overlay)


# 刷新 flat overlay
def _refresh_overlay(config: dict[str, Any]) -> None:
    global _overlay
    flat = config.get("flat") if isinstance(config.get("flat"), dict) else config
    # 空串必须保留：它是管理端「清除密钥」写入的显式值，
    # 若在此过滤，get_settings() 会回落 .env 中的旧密钥——界面显示已清除、请求仍带旧 Key。
    # 仅 None（DB 未设置该字段）跳过，让 base Settings 默认值生效。
    _overlay = {
        field: flat[field]
        for field in model_config_field_names()
        if field in flat and flat[field] is not None
    }


# 刷新路由快照
def _refresh_routing_snapshot(
    channels: list[SystemModelChannel],
    logical_models: list[LogicalModel],
    default_models: DefaultModels,
) -> None:
    global _routing_snapshot
    _routing_snapshot = RoutingSnapshot(
        channels=channels,
        logical_models=logical_models,
        default_models=default_models,
    )


# 从 env 构建默认渠道（开源版仅 TokenFree）
def _bootstrap_channels_from_env(settings: Settings | None = None) -> list[SystemModelChannel]:
    from app.services.tokenfree_gateway import locked_tokenfree_channel
    from app.services.media_model_presets import all_preset_channel_models

    src = settings or get_settings()
    key = (src.openai_api_key or src.ark_api_key or "").strip()
    models = all_preset_channel_models()
    return [locked_tokenfree_channel(api_key=key, models=models, enabled=True)]


def _seedance_logical_meta(upstream: str) -> tuple[str, str]:
    """按接入点 ID 推断 Seedance 逻辑模型（2.0 vs 2.5 vs Mini）；id 与 TokenFree 点分目录对齐。"""
    mid = (upstream or "").strip().lower()
    if "mini" in mid:
        # TokenFree→Ark mini 未开通，逻辑名并入 2.0
        return "seedance-2.0", "Seedance 2.0"
    if "fast" in mid:
        return "seedance-2.0-fast", "Seedance 2.0 Fast"
    if any(token in mid for token in ("2-5", "2.5", "260628")):
        return "seedance-2.5", "Seedance 2.5"
    if any(token in mid for token in ("2-0", "2.0", "260128", "seedance-2")):
        return "seedance-2.0", "Seedance 2.0"
    return "seedance-2.5", "Seedance 2.5"


def _append_seedance_alias(
    alias_models: list[LogicalModel],
    *,
    upstream: str,
    logical_id: str,
    name: str,
    bindings_for_upstream,
) -> None:
    if any(model.id == logical_id for model in alias_models):
        return
    bindings = bindings_for_upstream(upstream)
    if not bindings:
        return
    alias_models.append(
        LogicalModel(
            id=logical_id,
            name=name,
            capability="video",
            enabled=True,
            bindings=bindings,
        )
    )


def _merge_friendly_alias_models(
    synced: list[LogicalModel],
    aliases: list[LogicalModel],
) -> list[LogicalModel]:
    """保留 seedance/seedream 友好别名；去掉与别名同上游的 raw endpoint 重复项。"""
    from app.services.model_routing_config import normalize_model_name

    if not aliases:
        return synced
    alias_ids = {model.id for model in aliases}
    alias_upstream = {
        normalize_model_name(binding.upstream_model)
        for alias in aliases
        for binding in alias.bindings
    }
    merged: list[LogicalModel] = []
    seen_ids: set[str] = set()
    for model in synced:
        model_key = normalize_model_name(model.id)
        binding_keys = {normalize_model_name(b.upstream_model) for b in model.bindings}
        if (
            model.capability in {"video", "image"}
            and model.id not in alias_ids
            and binding_keys
            and binding_keys <= alias_upstream
            and model_key in binding_keys
        ):
            continue
        if model.id.lower() in seen_ids:
            continue
        merged.append(model)
        seen_ids.add(model.id.lower())
    for alias in aliases:
        if alias.id.lower() in seen_ids:
            continue
        merged.append(alias)
        seen_ids.add(alias.id.lower())
    return merged


# TokenFree 价目真实 id（兼容旧前端别名 → 同名逻辑模型，不做换绑）
_TOKENFREE_IMAGE_IDS: dict[str, str] = {
    "seedream-5.0": "seedream-5-0-pro",
    "seedream-5": "seedream-5-0-pro",
    "5.0": "seedream-5-0-pro",
    "seedream-4.5": "seedream-4-5",
    "seedream-4": "seedream-4-5",
    "4.5": "seedream-4-5",
}


# 从 env 构建默认逻辑模型与默认模型 ID
def _bootstrap_logical_from_channels(channels: list[SystemModelChannel]) -> tuple[list[LogicalModel], DefaultModels]:
    logical_models = synchronize_logical_models_with_channels([], channels)
    settings = get_settings()
    alias_models: list[LogicalModel] = []

    def _bindings_for_upstream(upstream: str) -> list:
        from app.services.tokenfree_pricing import canonicalize_channel_model_id

        wanted = {(upstream or "").strip(), canonicalize_channel_model_id(upstream)}
        wanted.discard("")
        result = []
        for model in logical_models:
            for binding in model.bindings:
                raw = (binding.upstream_model or "").strip()
                if raw in wanted or canonicalize_channel_model_id(raw) in wanted:
                    result.append(binding.model_copy())
        return result

    # 图模不再造 seedream-5.0 友好别名；仅视频保留 Seedance 短名
    if settings.model_video:
        logical_id, name = _seedance_logical_meta(settings.model_video)
        _append_seedance_alias(
            alias_models,
            upstream=settings.model_video,
            logical_id=logical_id,
            name=name,
            bindings_for_upstream=_bindings_for_upstream,
        )
    if settings.model_video_2:
        logical_id_2, name_2 = _seedance_logical_meta(settings.model_video_2)
        _append_seedance_alias(
            alias_models,
            upstream=settings.model_video_2,
            logical_id=logical_id_2,
            name=name_2,
            bindings_for_upstream=_bindings_for_upstream,
        )
    default_video = ""
    if settings.model_video:
        default_video, _ = _seedance_logical_meta(settings.model_video)
    elif settings.model_video_2:
        default_video = "seedance-2.0"
    synced = synchronize_logical_models_with_channels(logical_models, channels)
    merged = _merge_friendly_alias_models(synced, alias_models)
    # 默认图模 = 渠道/价目真实名（如 seedream-5-0-pro）
    image_default = (settings.model_image or "").strip()
    image_default = _TOKENFREE_IMAGE_IDS.get(image_default.lower(), image_default)
    defaults = DefaultModels(
        text_model=settings.model_llm,
        image_model=image_default,
        video_model=default_video,
        audio_model=settings.model_audio,
    )
    return merged, normalize_default_models(defaults, merged, channels)


# 历史渠道协议已下线，读库时收成仍支持的值，避免校验失败
_LIVE_CHANNEL_PROTOCOLS = {"openai", "auto"}
_LIVE_API_FORMATS = {"openai"}


def _coerce_channel_protocol(value: str | None) -> str:
    """把已删除的渠道协议收成 auto。"""
    raw = (value or "auto").strip().lower() or "auto"
    return raw if raw in _LIVE_CHANNEL_PROTOCOLS else "auto"


def _coerce_api_format(value: str | None) -> str:
    """把已删除的调用格式收成 openai。"""
    raw = (value or "openai").strip().lower() or "openai"
    return raw if raw in _LIVE_API_FORMATS else "openai"


# ORM 行转领域模型（admin 视图，密钥打码）
def _channel_row_to_admin(row: SystemModelChannelRow) -> SystemModelChannel:
    api_key = _decrypt_secret(row.api_key_ciphertext or "")
    return SystemModelChannel(
        id=row.id,
        name=row.name,
        base_url=row.base_url or "",
        api_key="",
        has_api_key=bool(api_key),
        api_format=_coerce_api_format(row.api_format),
        protocol=_coerce_channel_protocol(row.protocol),
        models=list(row.models or []),
        enabled=bool(row.enabled),
        sort_order=int(row.sort_order or 0),
        advanced_config=SystemChannelAdvancedConfig.model_validate(row.advanced_config)
        if row.advanced_config
        else None,
    )


# 运行时渠道（含明文密钥）
def _channel_row_to_runtime(row: SystemModelChannelRow) -> SystemModelChannel:
    channel = _channel_row_to_admin(row)
    return channel.model_copy(update={"api_key": _decrypt_secret(row.api_key_ciphertext or "")})


async def _get_or_create_app_row(db: AsyncSession) -> AppSettings:
    row = (await db.execute(select(AppSettings).where(AppSettings.id == "default"))).scalar_one_or_none()
    if row:
        return row
    row = AppSettings(id="default", config_json={"flat": _settings_to_dict()})
    db.add(row)
    await db.flush()
    return row


async def _load_channels(db: AsyncSession, *, runtime: bool) -> list[SystemModelChannel]:
    rows = list(
        (await db.execute(select(SystemModelChannelRow).order_by(SystemModelChannelRow.sort_order, SystemModelChannelRow.id)))
        .scalars()
        .all()
    )
    if not rows:
        return []
    if runtime:
        return [_channel_row_to_runtime(row) for row in rows]
    return [_channel_row_to_admin(row) for row in rows]


async def _ensure_bootstrapped_channels(db: AsyncSession) -> list[SystemModelChannelRow]:
    existing = list((await db.execute(select(SystemModelChannelRow))).scalars().all())
    if existing:
        return existing
    channels = _bootstrap_channels_from_env()
    rows: list[SystemModelChannelRow] = []
    for channel in channels:
        row = SystemModelChannelRow(
            id=channel.id,
            name=channel.name,
            base_url=channel.base_url,
            api_key_ciphertext=_encrypt_secret(channel.api_key) if channel.api_key else None,
            api_format=channel.api_format,
            protocol=channel.protocol,
            models=channel.models,
            enabled=channel.enabled,
            sort_order=channel.sort_order,
            advanced_config=channel.advanced_config.model_dump() if channel.advanced_config else None,
        )
        db.add(row)
        rows.append(row)
    logical_models, defaults = _bootstrap_logical_from_channels(channels)
    app_row = await _get_or_create_app_row(db)
    app_row.config_json = {
        "flat": _settings_to_dict(),
        "logical_models": [model.model_dump() for model in logical_models],
        "default_models": default_models_to_dict(defaults),
    }
    await db.commit()
    return rows



def _settings_to_dict(settings: Settings | None = None) -> dict[str, Any]:
    src = settings or get_settings()
    return {field: getattr(src, field) for field in model_config_field_names()}


def _decrypt_flat_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """解密 flat 密钥；解不开则删掉该字段，让运行时回落到 .env，避免空串/坏密文盖住环境变量。"""
    data = dict((raw or {}).get("flat") or raw or {})
    for field in SECRET_FIELDS:
        if field not in data or not data[field]:
            continue
        raw_val = str(data[field])
        plain = _decrypt_secret(raw_val)
        # 仍带 enc: 前缀说明密钥错配/双重加密，丢弃以免污染 overlay
        if raw_val.startswith(ENCRYPTED_PREFIX) and (not plain or plain.startswith(ENCRYPTED_PREFIX)):
            logger.warning("flat 密钥字段 %s 无法解密，已忽略并回落 .env", field)
            data.pop(field, None)
            continue
        data[field] = plain
    return data


def _encrypt_flat_config(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    for field in SECRET_FIELDS:
        value = data.get(field)
        if value:
            data[field] = _encrypt_secret(str(value))
    return data


def _effective_flat(stored: dict[str, Any] | None) -> dict[str, Any]:
    merged = _settings_to_dict()
    if stored:
        for field in model_config_field_names():
            if field in stored and stored[field] is not None:
                merged[field] = stored[field]
    return merged


async def _compose_runtime_state(db: AsyncSession) -> tuple[list[SystemModelChannel], list[LogicalModel], DefaultModels, dict[str, Any], AppSettings]:
    """组装手动所选配置的路由与完整运行时覆盖，不跨渠道回退。"""
    app_row = await _get_or_create_app_row(db)
    await _ensure_bootstrapped_channels(db)
    from app.services.provider_profiles import profile_state, profile_overlay

    channels = await _load_channels(db, runtime=True)
    config = dict(app_row.config_json or {})
    logical_models, default_models = profile_state(channels, default_models_from_dict(config.get("default_models")))
    flat = profile_overlay(_effective_flat(_decrypt_flat_config(config)), channels, default_models)
    return channels, logical_models, default_models, flat, app_row


async def load_model_settings_cache(db: AsyncSession) -> None:
    """加载路由快照；若与渠道不同步则回写 healed 配置，避免默认仍钉死旧模型名。"""
    channels, logical_models, default_models, flat, app_row = await _compose_runtime_state(db)
    config = dict(app_row.config_json or {})
    old_ids = {(item or {}).get("id") for item in (config.get("logical_models") or [])}
    new_ids = {model.id for model in logical_models}
    old_caps = {
        (item or {}).get("id"): (item or {}).get("capability")
        for item in (config.get("logical_models") or [])
    }
    new_caps = {model.id: model.capability for model in logical_models}
    old_defaults = default_models_from_dict(config.get("default_models"))
    flat_cfg = dict(config.get("flat") or {})
    flat_cfg.update({k: v for k, v in flat.items() if v is not None})
    flat_changed = flat_cfg != dict(config.get("flat") or {})
    if old_ids != new_ids or old_caps != new_caps or old_defaults != default_models or flat_changed:
        config["logical_models"] = [model.model_dump() for model in logical_models]
        config["default_models"] = default_models_to_dict(default_models)
        config["flat"] = _encrypt_flat_config(flat_cfg) if flat_cfg else config.get("flat")
        app_row.config_json = config
        await db.commit()
    _refresh_routing_snapshot(channels, logical_models, default_models)
    _refresh_overlay({"flat": flat})
    reload_settings()


def _build_readiness(
    channels: list[SystemModelChannel],
    logical_models: list[LogicalModel],
    defaults: DefaultModels,
) -> list[ModelCapabilityReadiness]:
    labels = {"text": "文本", "image": "图像", "video": "视频", "audio": "语音"}
    items: list[ModelCapabilityReadiness] = []
    for capability, attr in {
        "text": "text_model",
        "image": "image_model",
        "video": "video_model",
        "audio": "audio_model",
    }.items():
        cap: LogicalModelCapability = capability
        model_id = getattr(defaults, attr) or ""
        resolved = resolve_logical_model_config(logical_models, channels, cap, model_id) if model_id else None
        items.append(
            ModelCapabilityReadiness(
                capability=capability,
                label=labels[capability],
                model=model_id,
                ready=bool(resolved),
                message=f"路由至渠道 {resolved['channel'].name}" if resolved else f"需配置默认{labels[capability]}模型与渠道绑定",
            )
        )
    return items


def _to_admin_flat_out(flat: dict[str, Any], *, source: str, updated_at, channels, logical_models, defaults) -> AdminModelSettingsOut:
    payload = {field: flat.get(field) for field in model_config_field_names()}
    for field, flag in SECRET_FIELD_FLAGS.items():
        payload[field] = ""
        payload[flag] = bool(str(flat.get(field) or "").strip())
    payload["source"] = source
    payload["updated_at"] = updated_at
    payload["readiness"] = _build_readiness(channels, logical_models, defaults)
    return AdminModelSettingsOut.model_validate(payload)


async def get_admin_model_settings(db: AsyncSession) -> AdminModelSettingsOut:
    channels, logical_models, defaults, flat, app_row = await _compose_runtime_state(db)
    admin_channels = await _load_channels(db, runtime=False)
    source = "db" if app_row.config_json else "env"
    return _to_admin_flat_out(flat, source=source, updated_at=app_row.updated_at, channels=admin_channels, logical_models=logical_models, defaults=defaults)


async def get_admin_routing_settings(db: AsyncSession) -> AdminRoutingSettingsOut:
    channels, logical_models, defaults, _, app_row = await _compose_runtime_state(db)
    admin_channels = await _load_channels(db, runtime=False)
    errors = model_routing_validation_errors(logical_models, admin_channels or channels, defaults)
    return AdminRoutingSettingsOut(
        system_channels=admin_channels,
        logical_models=logical_models,
        default_models=defaults,
        validation_errors=errors,
        updated_at=app_row.updated_at,
    )


async def patch_admin_model_settings(
    db: AsyncSession,
    body: AdminModelSettingsPatch,
) -> tuple[AdminModelSettingsOut, list[str]]:
    app_row = await _get_or_create_app_row(db)
    config = dict(app_row.config_json or {})
    stored_flat = _decrypt_flat_config(config)
    current = _effective_flat(stored_flat if stored_flat else None)
    patch = body.model_dump(exclude_unset=True)
    applied: list[str] = []

    for field in SECRET_FIELDS:
        clear_flag = f"clear_{field}"
        if patch.pop(clear_flag, False):
            current[field] = ""
            applied.append(clear_flag)
        value = patch.pop(field, None)
        if value is not None and str(value).strip():
            current[field] = str(value).strip()
            applied.append(field)

    for field, value in patch.items():
        if value is None:
            continue
        current[field] = value
        applied.append(field)

    config["flat"] = _encrypt_flat_config(current)
    app_row.config_json = config
    await db.commit()
    await load_model_settings_cache(db)
    channels, logical_models, defaults, flat, app_row = await _compose_runtime_state(db)
    admin_channels = await _load_channels(db, runtime=False)
    return _to_admin_flat_out(flat, source="db", updated_at=app_row.updated_at, channels=admin_channels, logical_models=logical_models, defaults=defaults), applied


def _flat_from_env_settings() -> dict[str, Any]:
    # 读取进程环境 / .env（不经 DB overlay）
    env = Settings()
    return {field: getattr(env, field) for field in model_config_field_names()}


async def import_admin_model_settings_from_env(
    db: AsyncSession,
) -> tuple[AdminModelSettingsOut, list[str], list[str]]:
    """将 .env 中可管理字段写入 app_settings.flat（密钥加密存库）。"""
    app_row = await _get_or_create_app_row(db)
    config = dict(app_row.config_json or {})
    current = _effective_flat(_decrypt_flat_config(config))
    env_flat = _flat_from_env_settings()
    imported: list[str] = []
    skipped_secrets: list[str] = []

    for field in model_config_field_names():
        value = env_flat[field]
        if field in SECRET_FIELDS:
            if not str(value or "").strip():
                skipped_secrets.append(field)
                continue
        current[field] = value
        imported.append(field)

    config["flat"] = _encrypt_flat_config(current)
    app_row.config_json = config
    await db.commit()
    await load_model_settings_cache(db)
    channels, logical_models, defaults, flat, app_row = await _compose_runtime_state(db)
    admin_channels = await _load_channels(db, runtime=False)
    out = _to_admin_flat_out(
        flat,
        source="db",
        updated_at=app_row.updated_at,
        channels=admin_channels,
        logical_models=logical_models,
        defaults=defaults,
    )
    logger.info("imported %d fields from env, skipped %d empty secrets", len(imported), len(skipped_secrets))
    return out, imported, skipped_secrets


async def patch_admin_routing_settings(
    db: AsyncSession,
    body: AdminRoutingSettingsPatch,
) -> tuple[AdminRoutingSettingsOut, list[str]]:
    from app.models_tasks import TaskRun
    from app.services.tasks.service import TERMINAL_TASK_STATUSES
    from app.services.provider_profiles import normalize_profiles, profile_state

    # 先校验整份请求，错误配置不能留下部分写入。
    if body.logical_models is not None:
        raise ValueError("请通过渠道配置选择模型")
    if body.system_channels is None:
        raise ValueError("请提交完整渠道配置")
    items = normalize_profiles(body.system_channels)
    from sqlalchemy import text

    await db.execute(text("LOCK TABLE task_runs IN SHARE ROW EXCLUSIVE MODE"))
    busy = await db.scalar(select(TaskRun.id).where(TaskRun.status.notin_(TERMINAL_TASK_STATUSES)).limit(1))
    if busy:
        raise ValueError("有未结束任务，请等待任务结束后再修改渠道配置")
    app_row = await _get_or_create_app_row(db)
    existing_rows = {row.id: row for row in (await db.execute(select(SystemModelChannelRow))).scalars()}
    # 整组预检结束前不修改任一渠道行。
    for item in items:
        previous = existing_rows.get(item.id)
        previous_base = (previous.base_url or "").rstrip("/") if previous else ""
        if previous_base and previous_base != item.base_url and not (item.api_key or "").strip():
            raise ValueError("更换平台或区域时请重新填写 API Key")
    for item in items:
        row = existing_rows.get(item.id) or SystemModelChannelRow(id=item.id)
        for field in ("name", "base_url", "api_format", "protocol", "models", "enabled", "sort_order"):
            setattr(row, field, getattr(item, field))
        row.advanced_config = item.advanced_config.model_dump()
        if item.clear_api_key:
            row.api_key_ciphertext = None
        elif (item.api_key or "").strip():
            row.api_key_ciphertext = _encrypt_secret(item.api_key.strip())
        db.add(row)
    # 列表为完整替换；UI 删除的配置从数据库删除，不再参与路由。
    ids = {item.id for item in items}
    for cid, row in existing_rows.items():
        if cid not in ids:
            await db.delete(row)
    await db.flush()
    channels = await _load_channels(db, runtime=True)
    config = dict(app_row.config_json or {})
    logical, defaults = profile_state(channels, body.default_models or default_models_from_dict(config.get("default_models")))
    config["logical_models"] = [model.model_dump() for model in logical]
    config["default_models"] = default_models_to_dict(defaults)
    app_row.config_json = config
    await db.commit()
    await load_model_settings_cache(db)
    return await get_admin_routing_settings(db), ["system_channels", "default_models"]
