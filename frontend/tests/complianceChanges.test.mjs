import assert from 'node:assert/strict'
import { test } from 'node:test'
import { replacementPreview, canUndoReplacement, undoReplacement } from '../src/lib/complianceChanges.ts'

const row = { scope: 'shot', shot_id: 12, field: 'narration', matches_current: true, text: '😀好用的暖壶，高端设计，高端配置' }
const first = { id: '0', old: '最好用', new: '好用', current_start: 1, current_end: 3, undone: false }

test('高亮使用Unicode码点，emoji前缀不截断新词', () => {
  assert.deepEqual(replacementPreview(row, first), { before: '😀', match: '好用', after: '的暖壶，高端设计，高端配置' })
})

test('撤销长度变化后高亮服务端更新的位置，重复词只命中指定一处', () => {
  const updated = { ...row, text: '😀最好用的暖壶，顶级设计，高端配置' }
  const change = { id: '2', old: '顶级', new: '高端', current_start: 13, current_end: 15, undone: false }
  assert.deepEqual(replacementPreview(updated, change), { before: '😀最好用的暖壶，顶级设计，', match: '高端', after: '配置' })
})

test('单条撤销只向对应分镜提交服务端记录ID，不提交原稿或本地推测文本', async () => {
  const calls = []
  await undoReplacement(7, row, first, async (path, body) => { calls.push([path, body]) })
  assert.deepEqual(calls, [['/api/projects/7/shots/12', { compliance_undo: { field: 'narration', change_id: '0' } }]])
})

test('项目标题撤销路由与手改冲突有明确行为', async () => {
  const calls = []
  await undoReplacement(7, { ...row, scope: 'project', shot_id: null, field: 'title' }, first, async (path, body) => { calls.push([path, body]) })
  assert.equal(calls[0][0], '/api/projects/7')
  assert.equal(canUndoReplacement({ ...row, matches_current: false }, first), false)
  await assert.rejects(undoReplacement(7, { ...row, matches_current: false }, first, async () => { throw new Error('不应发送') }), /已编辑/)
  assert.equal(canUndoReplacement(row, { ...first, undone: true }), false)
})
