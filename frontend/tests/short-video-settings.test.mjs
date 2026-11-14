import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveShortVideoMediaSettings, shortVideoPipelineMode } from '../src/lib/shortVideoSettings.ts'

const image = { id: 'image-ref', label: 'Reference images', available: true, reference_limits: { images: 4 }, allowed_aspect_ratios: ['9:16', '16:9', '1:1'], available_resolutions: ['2K', '4K'], defaults: { resolution: '2K' } }
const video = { id: 'video-ref', label: 'Image to video', available: true, reference_limits: { images: 2 }, allowed_aspect_ratios: ['16:9', '9:16'], available_resolutions: ['720p', '1080p'], defaults: { resolution: '720p' } }
const catalog = { image_models: [image], video_models: [video], defaults: { image_model: image.id, video_model: video.id } }
const settings = { imageModel: image.id, videoModel: video.id, imageResolution: '2K', videoResolution: '720p', ratio: '9:16' }

test('new short videos default to image-to-video while explicit still-image projects retain their mode', () => {
  assert.equal(shortVideoPipelineMode(null), 'full')
  assert.equal(shortVideoPipelineMode('full'), 'full')
  assert.equal(shortVideoPipelineMode('image_text'), 'image_text')
  assert.equal(shortVideoPipelineMode('unknown'), 'full')
})

test('video settings require reference-capable models and a shared supported aspect ratio', () => {
  const result = resolveShortVideoMediaSettings(catalog, 'full', settings)
  assert.equal(result.ready, true)
  assert.deepEqual(result.ratios, ['9:16', '16:9'])
  assert.equal(result.imageModel, image.id)
  assert.equal(result.videoModel, video.id)
  assert.equal(result.ratio, '9:16')
})

test('new projects choose compatible defaults instead of unavailable or text-only models', () => {
  const textOnly = { ...image, id: 'text-only', reference_limits: { images: 0 } }
  const unavailable = { ...video, id: 'offline', available: false }
  const result = resolveShortVideoMediaSettings({ ...catalog, image_models: [textOnly, image], video_models: [unavailable, video], defaults: { image_model: textOnly.id, video_model: unavailable.id } }, 'full', { ...settings, imageModel: '', videoModel: '' })
  assert.equal(result.imageModel, image.id)
  assert.equal(result.videoModel, video.id)
  assert.deepEqual(result.imageOptions.map(model => model.id), [image.id])
  assert.deepEqual(result.videoOptions.map(model => model.id), [video.id])
  assert.equal(result.ready, true)
})

test('explicit unavailable or removed models remain selected and block generation until replaced', () => {
  for (const imageModel of ['removed-image', 'offline-image', 'text-only']) {
    const result = resolveShortVideoMediaSettings({ ...catalog, image_models: [image, { ...image, id: 'offline-image', available: false }, { ...image, id: 'text-only', reference_limits: { images: 0 } }] }, 'full', { ...settings, imageModel, imageResolution: 'saved-quality' })
    assert.equal(result.imageModel, imageModel)
    assert.equal(result.imageResolution, 'saved-quality')
    assert.equal(result.imageSelectionUnavailable, true)
    assert.equal(result.ready, false)
  }
  const result = resolveShortVideoMediaSettings(catalog, 'full', { ...settings, videoModel: 'removed-video', videoResolution: 'saved-video-quality' })
  assert.equal(result.videoModel, 'removed-video')
  assert.equal(result.videoResolution, 'saved-video-quality')
  assert.equal(result.videoSelectionUnavailable, true)
  assert.equal(result.ready, false)
})

test('continuity requires two image slots per model and reserves a third image slot for an existing subject reference', () => {
  const capOne = { ...image, id: 'one-image-slot', reference_limits: { images: 1 } }
  const capTwo = { ...image, id: 'two-image-slots', reference_limits: { images: 2 } }
  const capOneVideo = { ...video, id: 'one-video-slot', reference_limits: { images: 1 } }
  const selection = { ...settings, imageModel: '', videoModel: '' }
  const result = resolveShortVideoMediaSettings({ ...catalog, image_models: [capOne, capTwo, image], video_models: [capOneVideo, video] }, 'full', selection)
  assert.deepEqual(result.imageOptions.map(model => model.id), [capTwo.id, image.id])
  assert.deepEqual(result.videoOptions.map(model => model.id), [video.id])
  const subject = resolveShortVideoMediaSettings({ ...catalog, image_models: [capTwo, image] }, 'full', selection, 3)
  assert.deepEqual(subject.imageOptions.map(model => model.id), [image.id])
  assert.equal(subject.ready, true)
  assert.equal(resolveShortVideoMediaSettings({ ...catalog, image_models: [capOne] }, 'image_text', selection).ready, false)
})

test('model changes replace unsupported resolution and aspect settings with supported defaults', () => {
  const result = resolveShortVideoMediaSettings(catalog, 'full', { ...settings, imageResolution: '8K', videoResolution: '480p', ratio: '1:1' })
  assert.equal(result.imageResolution, '2K')
  assert.equal(result.videoResolution, '720p')
  assert.equal(result.ratio, '9:16')
})

test('still-image projects do not need a video model and keep their wider set of image ratios', () => {
  const result = resolveShortVideoMediaSettings({ ...catalog, video_models: [] }, 'image_text', { ...settings, ratio: '1:1' })
  assert.equal(result.ready, true)
  assert.equal(result.ratio, '1:1')
  assert.equal(resolveShortVideoMediaSettings({ ...catalog, video_models: [] }, 'full', settings).ready, false)
})

test('missing catalogs preserve saved draft choices but cannot start media generation', () => {
  const result = resolveShortVideoMediaSettings(null, 'full', settings)
  assert.equal(result.ready, false)
  for (const [key, value] of Object.entries(settings)) assert.equal(result[key], value)
  assert.equal(resolveShortVideoMediaSettings({ ...catalog, image_models: [] }, 'full', settings).ready, false)
})

test('models with no shared ratio are blocked; legacy catalog reference limits remain compatible', () => {
  const incompatible = resolveShortVideoMediaSettings({ ...catalog, video_models: [{ ...video, allowed_aspect_ratios: ['21:9'] }] }, 'full', settings)
  assert.equal(incompatible.ready, false)
  assert.equal(incompatible.ratio, '')
  const legacy = resolveShortVideoMediaSettings({ ...catalog, image_models: [{ ...image, reference_limits: undefined }], video_models: [{ ...video, reference_limits: undefined }] }, 'full', settings)
  assert.equal(legacy.ready, true)
})
