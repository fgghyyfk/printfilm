import test from 'node:test'
import assert from 'node:assert/strict'
import { ecomJobErrorText } from '../src/lib/ecomJobs.ts'
import { translationTargets, translationProgress, canTranslateSet } from '../src/lib/ecomTranslate.ts'
import { zhEcomJobs } from '../src/i18n/locales/zh/ecomJobs.ts'
import { enEcomJobs } from '../src/i18n/locales/en/ecomJobs.ts'
import { zhEcomSet } from '../src/i18n/locales/zh/ecomSet.ts'
import { enEcomSet } from '../src/i18n/locales/en/ecomSet.ts'

// 套图翻译目标排除当前语言，仅开放中文、英文和日文。
test('translation targets exclude current language', () => {
  assert.deepEqual(translationTargets('zh'), ['en', 'ja'])
  assert.deepEqual(translationTargets('en'), ['zh', 'ja'])
  assert.deepEqual(translationTargets('none'), ['zh', 'en', 'ja'])
})

// 无文字项计入完成进度，但不能计入可下载图片数。
test('translation progress treats no-text items as completed without an output', () => {
  assert.deepEqual(translationProgress([
    { status: 'succeeded', result_url: '/static/translated.png' },
    { status: 'skipped', result_url: null },
    { status: 'running', result_url: null },
    { status: 'failed', result_url: null },
  ]), { finished: 2, downloadable: 1, skipped: 1 })
  assert.deepEqual(translationProgress([]), { finished: 0, downloadable: 0, skipped: 0 })
})

// 仅完整成功且有排版文案与底图的套图可翻译。
test('set translation requires completed copy and reusable backgrounds', () => {
  const item = { status: 'succeeded', base_url: '/static/base.jpg', title: '杯子', subtitle: '', bullets: [] }
  assert.equal(canTranslateSet({ status: 'done', items: [item] }), true)
  assert.equal(canTranslateSet({ status: 'partial', items: [item] }), false)
  assert.equal(canTranslateSet({ status: 'done', items: [{ ...item, base_url: null }] }), false)
  assert.equal(canTranslateSet({ status: 'done', items: [{ ...item, title: '' }] }), false)
  assert.equal(canTranslateSet({ status: 'done', items: [] }), false)
})

// 翻译界面中英键、非空文本与动态插值保持一致。
test('translation UI copy has matching bilingual keys and placeholders', () => {
  // 递归检查每个新增文案节点。
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
  check(zhEcomJobs.ecomJobs.translate, enEcomJobs.ecomJobs.translate)
  check(zhEcomSet.ecomSet.translate, enEcomSet.ecomSet.translate)
})

// 两条翻译路径共用固定诊断映射，保留原始服务端错误供计费分类。
test('translation diagnostics have localized messages', () => {
  for (const [message, key] of [
    ['翻译结果格式错误，请重试', 'translationFormat'],
    ['请先完成套图生成', 'setIncomplete'],
    ['图片识字超时，请减少图片后重试', 'ocrTimeout'],
    ['请先配置视觉模型', 'vision'],
  ]) {
    assert.equal(ecomJobErrorText(message, (path) => path), `ecomJobs.serverErrors.${key}`)
    assert.equal(zhEcomJobs.ecomJobs.serverErrors[key], message)
    assert.ok(enEcomJobs.ecomJobs.serverErrors[key])
  }
})
