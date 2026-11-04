import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pollToolRunPage, keepToolRunTerminal } from '../src/lib/toolRunPolling.ts'

// 真实串行调度器使用可控时钟与请求，避免浏览器/测试框架依赖。
test('当前页进行中才安排下一次，成功后停止', async () => {
  const callbacks = [], seen = []
  const responses = [{ items: [{ status: 'queued' }] }, { items: [{ status: 'succeeded' }] }]
  const stop = pollToolRunPage({ load: async () => responses.shift(), onData: value => seen.push(value),
    onError: assert.fail, schedule: cb => { callbacks.push(cb); return callbacks.length }, cancel: () => {} })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(callbacks.length, 1)
  callbacks.shift()()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(seen.at(-1).items[0].status, 'succeeded')
  assert.equal(callbacks.length, 0)
  stop()
})

test('翻页停止后丢弃旧请求与旧详情，终态不被迟到running降级', async () => {
  let resolve, signal
  const seen = []
  const stop = pollToolRunPage({ load: requestSignal => { signal = requestSignal; return new Promise(r => { resolve = r }) },
    onData: value => seen.push(value), onError: assert.fail })
  stop()
  resolve({ items: [{ status: 'queued' }] })
  await new Promise(r => setImmediate(r))
  assert.equal(signal.aborted, true)
  assert.equal(seen.length, 0)
  assert.equal(keepToolRunTerminal({ id: 1, status: 'succeeded' }, { id: 1, status: 'running' }).status, 'succeeded')
})
