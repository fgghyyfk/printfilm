import test from 'node:test'
import assert from 'node:assert/strict'
import { copyLimits, validateSlotSelection, parseRefineSource } from '../src/lib/ecomSet.ts'
import { zhEcomSet } from '../src/i18n/locales/zh/ecomSet.ts'
import { enEcomSet } from '../src/i18n/locales/en/ecomSet.ts'

// 语言长度与后端一致，自定义列表不能缺主图或重复。
test('copy limits and custom slots', () => {
  assert.equal(copyLimits('en').title, 24)
  assert.equal(copyLimits('ja').subtitle, 20)
  const slots = [{ id: 'main_white', is_main: true }, { id: 'ad_banner', is_main: false }, { id: 'ad_square', is_main: false }]
  assert.equal(validateSlotSelection(slots.map(s => s.id), slots), true)
  assert.equal(validateSlotSelection(['ad_banner', 'ad_square', 'ad_square'], slots), false)
  assert.equal(validateSlotSelection(['main_white', 'ad_square', 'unknown'], slots), false)
  assert.equal(validateSlotSelection([], slots), false)
})

// 精修来源允许第零张，拒绝不完整或非法标识。
test('refine source URL identifiers', () => {
  assert.deepEqual(parseRefineSource('12', '0'), { jobId: 12, idx: 0 })
  assert.equal(parseRefineSource('12', null), null)
  assert.equal(parseRefineSource('12', '-1'), null)
  assert.equal(parseRefineSource('12', '1.5'), null)
})

// 新增界面文案中英齐全。
test('enhanced translations match', () => {
  assert.ok(zhEcomSet.ecomSet.enhanced)
  // 递归核对翻译键及非空值。
  function check(zh, en) {
    assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
    for (const key of Object.keys(zh)) {
      if (typeof zh[key] === 'object') check(zh[key], en[key])
      else assert.ok(typeof en[key] === 'string' && en[key].length)
    }
  }
  check(zhEcomSet.ecomSet.enhanced, enEcomSet.ecomSet.enhanced)
})
