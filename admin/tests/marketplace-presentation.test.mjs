import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { MarketplaceRequestError } from '../src/features/marketplace/errors.ts'
import { fieldStatusLabel, recordStatus, statusLabel, statusOptions } from '../src/features/marketplace/presentation.ts'

test('相同 pending 状态按审核、支付、打款、任务、举报区分含义', () => {
  assert.equal(statusLabel('works', 'pending'), '待审核')
  assert.equal(statusLabel('orders', 'pending'), '待支付')
  assert.equal(statusLabel('payouts', 'pending'), '待打款')
  assert.equal(statusLabel('previews', 'pending'), '排队中')
  assert.equal(statusLabel('reports', 'pending'), '待处理')
  assert.equal(statusLabel('orders', 'paid'), '已支付')
  assert.equal(statusLabel('payouts', 'paid'), '已打款')
})

test('状态筛选仅提供当前记录类型支持的状态，不混入收益方式', () => {
  assert.deepEqual(statusOptions('orders').map(([key]) => key), ['pending', 'paid', 'failed'])
  assert.deepEqual(statusOptions('reports').map(([key]) => key), ['pending', 'resolved'])
  assert.deepEqual(statusOptions('events'), [])
  assert.deepEqual(statusOptions('ledger'), [])
  assert.ok(!statusOptions('works').some(([key]) => key === 'copyright_contact'))
})

test('权益使用 active、账本使用结算状态，媒体准备不误用待审核', () => {
  assert.equal(recordStatus('entitlements', { active: false }), 'inactive')
  assert.equal(recordStatus('ledger', { settlement_status: 'pending' }), 'pending')
  assert.equal(statusLabel('ledger', recordStatus('ledger', { settlement_status: 'pending' })), '待结算')
  assert.equal(fieldStatusLabel('preview_status', 'pending'), '排队中')
  assert.equal(fieldStatusLabel('audit_status', 'pending'), '待审核')
})

test('业务错误翻译为中文并保留错误码及 HTTP 状态', () => {
  const error = new MarketplaceRequestError('MARKETPLACE_REVENUE_CONFIG_REQUIRED', 409)
  assert.match(error.message, /配置并启用平台抽成比例/)
  assert.equal(error.code, 'MARKETPLACE_REVENUE_CONFIG_REQUIRED')
  assert.equal(error.status, 409)
  const contextual = new MarketplaceRequestError('MARKETPLACE_SOURCE_NOT_READY:第3集仍有分镜未完成', 409)
  assert.equal(contextual.message, '第3集仍有分镜未完成')
  assert.equal(contextual.code, 'MARKETPLACE_SOURCE_NOT_READY')
})

test('结构化业务错误与未知错误码不向界面显示内部编码', () => {
  const error = new MarketplaceRequestError({ code: 'MARKETPLACE_STORAGE_UNAVAILABLE', message: '' }, 503)
  assert.match(error.message, /文件服务暂未配置/)
  assert.equal(error.code, 'MARKETPLACE_STORAGE_UNAVAILABLE')
  const unknown = new MarketplaceRequestError('MARKETPLACE_FUTURE_ERROR', 503)
  assert.ok(!unknown.message.includes('MARKETPLACE_'))
  assert.equal(unknown.code, 'MARKETPLACE_FUTURE_ERROR')
})

test('表单校验提示包含中文字段名与实际限制', () => {
  const error = new MarketplaceRequestError([{ loc: ['body', 'platform_share_bps'], msg: 'Input should be less than or equal to 10000' }], 422)
  assert.equal(error.message, '平台抽成比例：数值不能大于 10000')
  assert.equal(new MarketplaceRequestError([{ loc: ['body', 'reason'], msg: 'Field required' }], 422).message, '审核原因：这是必填项')
})

test('金额校验的分单位转换为页面使用的元，兼容 type 与旧 msg 两种校验格式', () => {
  const structured = new MarketplaceRequestError([{ loc: ['body', 'amount_fen'], type: 'greater_than_equal', ctx: { ge: 1 } }], 422)
  assert.equal(structured.message, '金额：数值不能小于 ¥0.01')
  const legacy = new MarketplaceRequestError([{ loc: ['body', 'amount_fen'], msg: 'Input should be less than or equal to 100000' }], 422)
  assert.equal(legacy.message, '金额：数值不能大于 ¥1000.00')
})

test('无效校验结构、非字符串消息和不完整限制参数均安全回退', () => {
  for (const detail of [null, undefined, 42, false, { message: null }, [], [null, 'invalid', []], [{ loc: 'body', msg: {} }], [{ loc: {}, msg: 123 }]]) {
    assert.doesNotThrow(() => new MarketplaceRequestError(detail, 422))
    assert.match(new MarketplaceRequestError(detail, 422).message, /检查|必填/)
  }
  const malformed = new MarketplaceRequestError([{ loc: ['body', null, {}, 'amount_fen'], type: 'less_than_equal', ctx: { le: {} } }], 422)
  assert.ok(!malformed.message.includes('undefined'))
  assert.ok(!malformed.message.includes('[object Object]'))
  assert.ok(!malformed.message.includes('NaN'))
})

test('未识别的纯英文错误使用中文提示，中文业务说明仍保留', () => {
  const error = new MarketplaceRequestError('Internal Server Error', 503)
  assert.equal(error.message, '暂时无法完成操作，请稍后重试。')
  assert.equal(new MarketplaceRequestError({ message: 'Permission denied' }, 403).message, '当前账号没有执行此操作的权限。')
  assert.equal(new MarketplaceRequestError('请先补齐第2集封面', 409).message, '请先补齐第2集封面')
})

// 只替换运行环境依赖，使用真实请求实现验证响应和网络失败处理。
const apiSource = readFileSync(new URL('../src/features/marketplace/api.ts', import.meta.url), 'utf8')
  .replace(/^import .* from 'react'\r?\n/m, '')
  .replace(/^import .* from '@\/lib\/auth'\r?\n/m, "const getToken = () => 'test-token'\n")
  .replace("from './errors'", `from ${JSON.stringify(new URL('../src/features/marketplace/errors.ts', import.meta.url).href)}`)
const api = await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(apiSource)).toString('base64')}`)

test('JSON null 错误响应不会抛出属性读取错误，请求和授权函均返回中文业务错误', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => new Response('null', { status: 503, headers: { 'content-type': 'application/json' } })
  try {
    await assert.rejects(api.request('/api/admin/marketplace/support'), error => error instanceof MarketplaceRequestError && error.status === 503 && error.message === '暂时无法完成操作，请稍后重试。')
    await assert.rejects(api.letterFile(1), error => error instanceof MarketplaceRequestError && error.status === 503 && /授权函暂时无法打开/.test(error.message))
  } finally {
    globalThis.fetch = previous
  }
})

test('请求和授权函网络失败显示中文，保留原始 cause 便于排查', async () => {
  const previous = globalThis.fetch
  const cause = new TypeError('Failed to fetch')
  globalThis.fetch = async () => { throw cause }
  try {
    await assert.rejects(api.request('/api/admin/marketplace/support'), error => error instanceof MarketplaceRequestError && error.status === 0 && error.cause === cause && /检查网络/.test(error.message))
    await assert.rejects(api.letterFile(1), error => error instanceof MarketplaceRequestError && error.status === 0 && error.cause === cause && /授权函.*检查网络/.test(error.message))
  } finally {
    globalThis.fetch = previous
  }
})
