import assert from 'node:assert/strict'
import { test } from 'node:test'
import { hasActiveUnifiedTasks, isProjectBusy, requestProjectCancellation } from '../src/lib/kepuTaskControls.ts'

// 模拟后端运行态响应，不依赖页面源码或浏览器。
const project = (status, tasks) => ({ id: 7, status, active_tasks: tasks })

test('取消请求未终止前，继续忙碌并等待停止', () => {
  const p = project('CANCELLED', [{ status: 'cancel_requested', cancel_requested: true }])
  assert.equal(hasActiveUnifiedTasks(p), true)
  assert.equal(isProjectBusy(p), true)
  assert.equal(isProjectBusy(project('IMAGING', [{ status: 'running', cancel_requested: true }])), true)
})

test('空活动列表允许恢复操作，终态任务也不再锁住页面', () => {
  assert.equal(isProjectBusy(project('IMAGING', [])), false)
  assert.equal(isProjectBusy(project('IMAGING', [{ status: 'cancelled', cancel_requested: true }])), false)
})

test('取消生成先二次确认，拒绝确认时不发请求', async () => {
  let calls = 0
  const result = await requestProjectCancellation(7, async (options) => {
    assert.equal(options.confirmText, '取消生成')
    assert.match(options.message, /停止/)
    return false
  }, async () => { calls += 1 })
  assert.equal(result, null)
  assert.equal(calls, 0)
})

test('确认后调用当前项目取消API，保留服务端等待停止的状态', async () => {
  const order = []
  const response = project('IMAGING', [{ status: 'cancel_requested', cancel_requested: true }])
  const result = await requestProjectCancellation(7, async () => { order.push('confirm'); return true }, async (id) => {
    assert.equal(id, 7)
    order.push('cancel')
    return response
  })
  assert.deepEqual(order, ['confirm', 'cancel'])
  assert.equal(result, response)
  assert.equal(isProjectBusy(result), true)
})

test('取消API失败会传回错误供页面显示，原项目仍然busy', async () => {
  const p = project('IMAGING', [{ status: 'running' }])
  await assert.rejects(requestProjectCancellation(7, async () => true, async () => { throw new Error('取消失败') }), /取消失败/)
  assert.equal(isProjectBusy(p), true)
})
