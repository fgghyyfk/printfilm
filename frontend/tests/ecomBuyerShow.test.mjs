import test from 'node:test'
import assert from 'node:assert/strict'
import { zhEcomJobs } from '../src/i18n/locales/zh/ecomJobs.ts'
import { enEcomJobs } from '../src/i18n/locales/en/ecomJobs.ts'
import { validateBuyerShowImages } from '../src/lib/ecomJobs.ts'

// 可选角色可以缺省，所有上传角色都有明确张数与类型限制。
test('buyer show validates product, optional model and scene roles', () => {
  const image = { type: 'image/png', size: 100 }
  assert.equal(validateBuyerShowImages([image], [], []), null)
  assert.equal(validateBuyerShowImages(Array(9).fill(image), [image], [image]), null)
  assert.equal(validateBuyerShowImages([], [], []), 'required')
  assert.equal(validateBuyerShowImages(Array(10).fill(image), [], []), 'count')
  assert.equal(validateBuyerShowImages([image], [image, image], []), 'optional')
  assert.equal(validateBuyerShowImages([image], [], [{ type: 'image/gif', size: 1 }]), 'type')
})

// 新工作台的每项文案必须在中文和英文同时提供。
test('buyer show translations match', () => {
  // 递归比较文案结构与非空翻译。
  function check(zh, en) {
    assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
    for (const key of Object.keys(zh)) {
      if (typeof zh[key] === 'object') check(zh[key], en[key])
      else assert.ok(typeof en[key] === 'string' && en[key].length)
    }
  }
  assert.ok(zhEcomJobs.ecomJobs.buyerShow)
  check(zhEcomJobs.ecomJobs.buyerShow, enEcomJobs.ecomJobs.buyerShow)
})
