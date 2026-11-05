import assert from 'node:assert/strict'
import test from 'node:test'
import { canUseJobVideo, ecomHistoryHref, nextStepHref } from '../src/lib/ecomHub.ts'

// 跳转只携带来源标识，所有目标有确定路由。
test('result actions preserve source identity for every next tool', () => {
  assert.equal(nextStepHref('set', 9, 0), '/daihuo?source_job_id=9&source_item_idx=0')
  assert.equal(nextStepHref('style', 9, 2), '/ecom/style?source_job_id=9&source_item_idx=2')
  assert.equal(nextStepHref('sku', 9, 2), '/ecom/sku?source_job_id=9&source_item_idx=2')
  for (const kind of ['refine', 'style', 'buyer_show', 'sku', 'translate']) assert.match(ecomHistoryHref(kind, 5), /\?id=5$/)
  assert.equal(ecomHistoryHref('set', 5), '/daihuo?id=5')
})

// 至少两张成功且有结果地址的图片才开放视频入口。
test('video requires two successful result URLs', () => {
  const success = { status: 'succeeded', result_url: '/static/a.png' }
  assert.equal(canUseJobVideo([success]), false)
  assert.equal(canUseJobVideo([success, { status: 'failed', result_url: '/static/b.png' }]), false)
  assert.equal(canUseJobVideo([success, { status: 'succeeded', result_url: null }]), false)
  assert.equal(canUseJobVideo([success, success]), true)
})


// 工作台及结果串联文案保持中英键一致，不出现缺失或空翻译。
test('hub translations have matching nonempty keys', async () => {
  const { zhEcomHub } = await import('../src/i18n/locales/zh/ecomHub.ts')
  const { enEcomHub } = await import('../src/i18n/locales/en/ecomHub.ts')
  // 递归比对真实界面文案。
  function check(zh, en) {
    assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
    for (const key of Object.keys(zh)) {
      if (typeof zh[key] === 'object') check(zh[key], en[key])
      else assert.ok(typeof en[key] === 'string' && en[key].length)
    }
  }
  check(zhEcomHub, enEcomHub)
})
