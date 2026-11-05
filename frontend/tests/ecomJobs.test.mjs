import test from 'node:test'
import assert from 'node:assert/strict'
import { validateEcomImages, ecomJobNeedsPolling } from '../src/lib/ecomJobs.ts'

// 上传批次必须整批校验，不能悄悄丢弃超限或无效图片。
test('validates image count, MIME and per-file size', () => {
  const image = { type: 'image/png', size: 100 }
  assert.equal(validateEcomImages([], 20), 'required')
  assert.equal(validateEcomImages(Array(21).fill(image), 20), 'count')
  assert.equal(validateEcomImages([{ type: 'text/plain', size: 1 }], 20), 'type')
  assert.equal(validateEcomImages([{ ...image, size: 10 * 1024 * 1024 + 1 }], 20), 'size')
  assert.equal(validateEcomImages([image], 20), null)
})

// 只有待处理任务持续轮询，所有终态停止。
test('polls active jobs only', () => {
  assert.equal(ecomJobNeedsPolling(null), false)
  for (const status of ['queued', 'running']) assert.equal(ecomJobNeedsPolling({ status }), true)
  for (const status of ['done', 'partial', 'failed']) assert.equal(ecomJobNeedsPolling({ status }), false)
})

// 公共中文错误按已知类别展示双语，不改写存储的原始诊断。
test('maps known server diagnostics without rewriting unknown messages', async () => {
  const { ecomJobErrorText } = await import('../src/lib/ecomJobs.ts')
  assert.equal(ecomJobErrorText('这张图正在生成中', (key) => key), 'ecomJobs.serverErrors.busy')
  assert.equal(ecomJobErrorText('请求的内容不存在', (key) => key), 'ecomJobs.serverErrors.notFound')
  assert.equal(ecomJobErrorText('Custom diagnostic', (key) => key), 'Custom diagnostic')
})

// 恢复对账写回的取消和通用失败文案也必须进入双语诊断映射。
test('localizes terminal task reconciliation diagnostics', async () => {
  const { ecomJobErrorText } = await import('../src/lib/ecomJobs.ts')
  assert.equal(ecomJobErrorText('任务已取消', (key) => key), 'ecomJobs.serverErrors.cancelled')
  assert.equal(ecomJobErrorText('生成失败，请重试', (key) => key), 'ecomJobs.serverErrors.generation')
})
