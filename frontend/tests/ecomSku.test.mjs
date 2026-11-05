import test from 'node:test'
import assert from 'node:assert/strict'
import { zhEcomJobs } from '../src/i18n/locales/zh/ecomJobs.ts'
import { enEcomJobs } from '../src/i18n/locales/en/ecomJobs.ts'
import { skuNamesForFiles, validateSkuImages } from '../src/lib/ecomSku.ts'

// 场景必须唯一，产品图为 1–20 张，角色均需格式校验。
test('SKU images require one scene and one to twenty products', () => {
  const image = { type: 'image/png', size: 100 }
  assert.equal(validateSkuImages([image], [image]), null)
  assert.equal(validateSkuImages(Array(20).fill(image), [image]), null)
  assert.equal(validateSkuImages([], [image]), 'required')
  assert.equal(validateSkuImages(Array(21).fill(image), [image]), 'count')
  assert.equal(validateSkuImages([image], []), 'sceneRequired')
  assert.equal(validateSkuImages([image], [image, image]), 'sceneRequired')
  assert.equal(validateSkuImages([image], [{ type: 'image/gif', size: 10 }]), 'type')
})

// 按文件对象保留编辑名，重名图片不可串 SKU。
test('SKU names default from filenames and preserve edits through removals', () => {
  const red = { name: '杯子.png' }, green = { name: '杯子.png' }, blue = { name: 'blue.sku.webp' }
  assert.deepEqual(skuNamesForFiles([red, green, blue]), ['杯子', '杯子', 'blue.sku'])
  assert.deepEqual(skuNamesForFiles([green, blue], [red, green], ['红杯', '绿杯']), ['绿杯', 'blue.sku'])
  assert.equal(skuNamesForFiles([{ name: 'x'.repeat(110) + '.png' }])[0].length, 100)
})

// 新工作台中英文案结构与占位符保持一致。
test('SKU translations match', () => {
  // 递归比对翻译结构。
  function check(zh, en) {
    assert.ok(zh && en)
    assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
    for (const key of Object.keys(zh)) {
      if (typeof zh[key] === 'object') check(zh[key], en[key])
      else {
        assert.ok(typeof en[key] === 'string' && en[key].length)
        assert.deepEqual(zh[key].match(/\{\w+\}/g), en[key].match(/\{\w+\}/g))
      }
    }
  }
  check(zhEcomJobs.ecomJobs.sku, enEcomJobs.ecomJobs.sku)
})
