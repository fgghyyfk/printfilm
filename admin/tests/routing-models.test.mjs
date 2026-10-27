import test from 'node:test'
import assert from 'node:assert/strict'
import { enabledPresetModels, normalizeRoutingDefaults, routingModelSettings, setRoutingModelEnabled } from '../src/lib/routingModels.ts'
import { CAPABILITY_ORDER, PRESET_MODELS } from '../src/lib/tokenfreeRecommendedModels.ts'

function settings() {
  return {
    system_channels: [],
    logical_models: CAPABILITY_ORDER.flatMap((capability) => PRESET_MODELS[capability].map(({ id, label }) => ({
      id, name: label, capability, strategy_id: `strategy-${id}`, enabled: true,
      bindings: [{ id: `binding-${id}`, channel_id: 'tokenfree', upstream_model: id, enabled: true, priority: 20, weight: 3 }],
    }))),
    default_models: Object.fromEntries(CAPABILITY_ORDER.map((capability) => [`${capability}_model`, PRESET_MODELS[capability][0].id])),
    media_reference_limits: { 'seedream-5.0-pro': { images: 9, videos: 0, audios: 0 } },
    validation_errors: [],
  }
}

test('停用当前默认模型后，只从启用的预设中选择下一个默认模型', () => {
  const original = settings()
  const updated = setRoutingModelEnabled(original, 'seedream-5.0-pro', false)
  assert.equal(updated.default_models.image_model, 'seedream-5.0')
  assert.deepEqual(enabledPresetModels('image', updated.logical_models).map(({ id }) => id), PRESET_MODELS.image.slice(1).map(({ id }) => id))
  assert.equal(updated.default_models.video_model, original.default_models.video_model)
  assert.equal(original.logical_models.find(({ id }) => id === 'seedream-5.0-pro').enabled, true)
})

test('停用非默认模型时保留当前默认模型', () => {
  const updated = setRoutingModelEnabled(settings(), 'seedream-5.0-flash', false)
  assert.equal(updated.default_models.image_model, 'seedream-5.0-pro')
})

test('某类模型全部停用后默认值为空，加载与保存都不会重新启用', () => {
  let updated = settings()
  for (const { id } of PRESET_MODELS.video) updated = setRoutingModelEnabled(updated, id, false)
  assert.equal(updated.default_models.video_model, '')
  assert.deepEqual(enabledPresetModels('video', updated.logical_models), [])
  assert.equal(normalizeRoutingDefaults({ ...updated.default_models, video_model: 'seedance-2.5' }, updated.logical_models).video_model, '')
  const payload = routingModelSettings(updated)
  assert.equal(payload.default_models.video_model, '')
  assert.ok(payload.logical_models.filter(({ capability }) => capability === 'video').every(({ enabled }) => !enabled))
})

test('重新启用模型后可作为默认模型，保存保留绑定、策略与参考上限', () => {
  const original = settings()
  const disabled = setRoutingModelEnabled(original, PRESET_MODELS.audio[0].id, false)
  const updated = setRoutingModelEnabled(disabled, PRESET_MODELS.audio[0].id, true)
  assert.equal(updated.default_models.audio_model, PRESET_MODELS.audio[0].id)
  const payload = routingModelSettings(updated)
  assert.deepEqual(payload.logical_models, original.logical_models)
  assert.deepEqual(payload.media_reference_limits, original.media_reference_limits)
})

test('历史别名可映射到启用的预设，但不会选中已停用模型', () => {
  const original = settings()
  const defaults = { ...original.default_models, image_model: 'seedream-5-0-flash' }
  assert.equal(normalizeRoutingDefaults(defaults, original.logical_models).image_model, 'seedream-5.0-flash')
  const disabled = setRoutingModelEnabled(original, 'seedream-5.0-flash', false)
  assert.equal(normalizeRoutingDefaults(defaults, disabled.logical_models).image_model, 'seedream-5.0-pro')
})
