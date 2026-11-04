import { loadSource } from './bundleModule.mjs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
const { dramaAssetHasImage, dramaAssetHasReferenceImage, dramaAssetNeedsImageGeneration } = await loadSource('../src/lib/dramaAssetImage.ts')

// 与视频后端实际接受的cover优先、位图后缀规则保持一致。
test('补缺图不把临时预览、无后缀地址或SVG当作可用参考图', () => {
  for (const asset of [
    { type: 'character', url: '', params: { mediaUrl: '/static/preview.png' } },
    { type: 'scene', url: 'https://cdn.test/signed-image' },
    { type: 'prop', url: 'data:image/png;base64,example' },
    { type: 'character', cover: '/static/placeholder.svg', url: '/static/old.png' },
  ]) {
    assert.equal(dramaAssetHasReferenceImage(asset), false)
    assert.equal(dramaAssetNeedsImageGeneration(asset), true)
  }
})

test('上传和生成的有效位图跳过，音色和视频不参加批量补图', () => {
  assert.equal(dramaAssetNeedsImageGeneration({ type: 'character', url: '/static/upload.JPG?size=1' }), false)
  assert.equal(dramaAssetHasImage({ cover: 'https://cdn.test/cover.webp?signature=example' }), true)
  for (const type of ['voice', 'video', 'audio', 'text']) {
    assert.equal(dramaAssetNeedsImageGeneration({ type }), false)
  }
})

test('无后缀和params图片仍可预览，不把可见图片称为未出图', () => {
  assert.equal(dramaAssetHasImage({ url: 'https://cdn.test/signed-image' }), true)
  assert.equal(dramaAssetHasImage({ params: { mediaUrl: '/static/preview.png' } }), true)
})
