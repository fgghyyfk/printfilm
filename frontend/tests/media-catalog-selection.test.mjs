import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveCatalogModelSelection } from '../src/lib/mediaCatalogSelection.ts'

const model = (id, available = true) => ({ id, available })
const catalog = (image_models = [], video_models = [], defaults = {}) => ({
  image_models, video_models, defaults: { image_model: '', video_model: '', ...defaults },
})

test('unknown catalog from an initial load or request failure preserves the draft selection', () => {
  assert.equal(resolveCatalogModelSelection(null, 'image', 'saved-image'), 'saved-image')
  assert.equal(resolveCatalogModelSelection(null, 'video', 'saved-video'), 'saved-video')
})

test('an authoritative empty capability clears a previously selected model without using a stale default', () => {
  const empty = catalog([], [], { image_model: 'disabled-image', video_model: 'disabled-video' })
  assert.equal(resolveCatalogModelSelection(empty, 'image', 'disabled-image'), '')
  assert.equal(resolveCatalogModelSelection(empty, 'video', 'disabled-video'), '')
})

test('catalog refresh preserves a valid choice and replaces removed models with a listed available default', () => {
  const refreshed = catalog([model('image-one'), model('image-two')], [model('video-one')], { image_model: 'image-two' })
  assert.equal(resolveCatalogModelSelection(refreshed, 'image', 'image-one'), 'image-one')
  assert.equal(resolveCatalogModelSelection(refreshed, 'image', 'removed-image'), 'image-two')
  assert.equal(resolveCatalogModelSelection(refreshed, 'video', 'removed-video'), 'video-one')
})

test('unavailable models and defaults outside the current catalog cannot become selected', () => {
  const partial = catalog([model('unpriced', false), model('usable')], [model('unpriced-video', false)], { image_model: 'unpriced', video_model: 'unpriced-video' })
  assert.equal(resolveCatalogModelSelection(partial, 'image', 'unpriced'), 'usable')
  assert.equal(resolveCatalogModelSelection(partial, 'video', 'unpriced-video'), '')
  partial.defaults.image_model = 'removed-default'
  assert.equal(resolveCatalogModelSelection(partial, 'image', ''), 'usable')
})
