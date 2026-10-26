import test from 'node:test'
import assert from 'node:assert/strict'
import { generationRecordKey, generationDetailPath, generationBillingState } from '../src/lib/generationTaskHistory.ts'
import { toolRunDetailAsset } from '../src/lib/toolRunDetails.ts'
import { scopedRequestInit, setWorkspaceTeam } from '../src/features/teams/workspaceScope.ts'

const record = { id: 42, tool_id: 'image', kind: 'image', status: 'succeeded', prompt: 'A portrait', urls: [], created_at: '2026-10-26T10:00:00Z' }

test('同数字 ID 的任务和旧工具记录使用独立标识及详情地址', () => {
  const task = { ...record, source: 'task' }
  assert.equal(generationRecordKey(task), 'task:42')
  assert.equal(generationRecordKey(record), 'tool:42')
  assert.equal(generationDetailPath(task), '/api/tasks/generations/42')
  assert.equal(generationDetailPath(record), '/api/tools/runs/42')
})

test('文字生成详情保留完整脚本，且不能把结果地址当图片预览', () => {
  const content = '镜头一\n完整生成脚本'.repeat(100)
  const asset = toolRunDetailAsset({ ...record, kind: 'text', text_content: content, urls: ['/static/result.txt'] })
  assert.equal(asset.type, 'text')
  assert.equal(asset.url, null)
  assert.equal(asset.params.textContent, content)
})

test('尚未产出媒体的任务详情显示可读状态或错误', () => {
  const queued = toolRunDetailAsset({ ...record, kind: 'video', status: 'queued', preview_url: '/static/frame.png' })
  assert.equal(queued.type, 'text')
  assert.equal(queued.url, null)
  assert.match(queued.params.textContent, /排队/)
  const failed = toolRunDetailAsset({ ...record, kind: 'audio', status: 'failed', error: '模型暂不可用' })
  assert.equal(failed.params.textContent, '模型暂不可用')
})

test('成功媒体仍按所选结果预览，视频首帧不能作为成片地址', () => {
  const asset = toolRunDetailAsset({ ...record, kind: 'video', urls: ['/static/a.mp4', '/static/b.mp4'], preview_url: '/static/frame.png' }, 1)
  assert.equal(asset.type, 'video')
  assert.equal(asset.url, '/static/b.mp4')
  assert.equal(asset.cover, '/static/frame.png')
})

test('结算状态不根据完成时间推断，隐藏费用不能显示成免费', () => {
  assert.equal(generationBillingState({ ...record, billing_status: 'pending', finished_at: record.created_at }), 'pending')
  assert.equal(generationBillingState({ ...record, billing_charged_fen: null }), 'hidden')
  assert.equal(generationBillingState({ ...record, status: 'failed', billing_charged_fen: 0 }), 'not_charged')
  assert.equal(generationBillingState({ ...record, billing_charged_fen: 500 }), 'settled')
})

test('统一任务列表和详情继承当前团队范围，切回个人时清除', () => {
  try {
    setWorkspaceTeam(12)
    assert.equal(new Headers(scopedRequestInit('/api/tasks/generations?page=1').headers).get('X-Team-Id'), '12')
    assert.equal(new Headers(scopedRequestInit(generationDetailPath({ ...record, source: 'task' })).headers).get('X-Team-Id'), '12')
    setWorkspaceTeam(null)
    assert.equal(new Headers(scopedRequestInit('/api/tasks/generations').headers).get('X-Team-Id'), null)
  } finally { setWorkspaceTeam(null) }
})
