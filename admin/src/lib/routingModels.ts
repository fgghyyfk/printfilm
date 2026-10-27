import type { AdminDefaultModels, AdminLogicalModel, AdminRoutingSettings } from "../api/types/settings.ts";
import { clampDefaultToPreset, PRESET_MODELS, type ModelCapability } from "./tokenfreeRecommendedModels.ts";

export function enabledPresetModels(capability: ModelCapability, models: readonly AdminLogicalModel[]) {
  return PRESET_MODELS[capability].filter((preset) => models.some((model) =>
    model.capability === capability && model.id === preset.id && model.enabled,
  ));
}

export function normalizeRoutingDefaults(raw: Partial<AdminDefaultModels> | undefined, models: readonly AdminLogicalModel[]): AdminDefaultModels {
  function defaultFor(capability: ModelCapability) {
    const options = enabledPresetModels(capability, models);
    const selected = clampDefaultToPreset(capability, raw?.[`${capability}_model`]);
    return options.find((model) => model.id === selected)?.id ?? options[0]?.id ?? "";
  }
  return {
    text_model: defaultFor("text"),
    image_model: defaultFor("image"),
    video_model: defaultFor("video"),
    audio_model: defaultFor("audio"),
  };
}

export function setRoutingModelEnabled(settings: AdminRoutingSettings, id: string, enabled: boolean): AdminRoutingSettings {
  const models = settings.logical_models.map((model) => model.id === id ? { ...model, enabled } : model);
  return { ...settings, logical_models: models, default_models: normalizeRoutingDefaults(settings.default_models, models) };
}

export function routingModelSettings(settings: AdminRoutingSettings) {
  return {
    logical_models: settings.logical_models,
    default_models: normalizeRoutingDefaults(settings.default_models, settings.logical_models),
    media_reference_limits: settings.media_reference_limits,
  };
}
