import test from 'node:test'
import assert from 'node:assert/strict'
import { styleOutputCount, styleProductLimit, validateEcomImages } from '../src/lib/ecomJobs.ts'
import { zhEcomJobs } from '../src/i18n/locales/zh/ecomJobs.ts'
import { enEcomJobs } from '../src/i18n/locales/en/ecomJobs.ts'

// 模式切换的产品上限和预估输出张数必须匹配后端契约。
test('style mode limits and output counts', () => {
  assert.equal(styleProductLimit('single'), 6)
  assert.equal(styleProductLimit('batch'), 20)
  assert.equal(styleOutputCount('single', 6, 4), 4)
  assert.equal(styleOutputCount('batch', 20, 1), 20)
  const image = { type: 'image/png', size: 100 }
  assert.equal(validateEcomImages(Array(7).fill(image), styleProductLimit('single')), 'count')
  assert.equal(validateEcomImages(Array(20).fill(image), styleProductLimit('batch')), null)
  assert.equal(validateEcomImages(Array(21).fill(image), styleProductLimit('batch')), 'count')
})

// 风格工作台文案在两种语言中必须都有对应项。
test('style translations have matching nonempty keys', () => {
  // 逐层比较界面文案，避免新页面仅在中文可用。
  function check(zh, en) {
    assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
    for (const key of Object.keys(zh)) {
      if (typeof zh[key] === 'object') check(zh[key], en[key])
      else assert.ok(typeof en[key] === 'string' && en[key].length)
    }
  }
  assert.ok(zhEcomJobs.ecomJobs.style)
  check(zhEcomJobs.ecomJobs.style, enEcomJobs.ecomJobs.style)
})
