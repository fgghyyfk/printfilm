import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activeCreationRun,
  buildCreationSubmission,
  creationMessageId,
  creationPhaseIndex,
  creationStatusLabel,
  isCreationBusy,
} from '../src/lib/creationSession.ts'

test('会话优先展示服务端指定版本，没有 active_run_id 时恢复最新版', () => {
  const runs = [
    { id: 1, version: 1, status: 'completed', phase: 'done' },
    { id: 2, version: 2, status: 'running', phase: 'assets' },
  ]
  assert.equal(activeCreationRun({ runs, active_run_id: '1' }).id, 1)
  assert.equal(activeCreationRun({ runs }).id, 2)
  assert.equal(activeCreationRun(null), undefined)
})

test('终态不会被旧版本运行状态阻塞，新任务或未知状态用当前版本恢复', () => {
  const runs = [{ id: 1, version: 1, status: 'running', phase: 'script' }]
  for (const status of ['completed', 'failed', 'cancelled', 'drafting'])
    assert.equal(isCreationBusy({ status, runs }), false)
  for (const status of ['thinking', 'generating'])
    assert.equal(isCreationBusy({ status, runs: [] }), true)
  assert.equal(isCreationBusy({ status: 'recovering', runs }), true)
  assert.equal(isCreationBusy(null), false)
})

test('阶段映射与旧 copy 命名兼容，未知阶段安全落到文案', () => {
  assert.equal(creationPhaseIndex('copy'), 0)
  assert.equal(creationPhaseIndex('script'), 1)
  assert.equal(creationPhaseIndex('assets'), 2)
  assert.equal(creationPhaseIndex('compose'), 3)
  assert.equal(creationPhaseIndex('done'), 4)
  assert.equal(creationPhaseIndex('new-phase'), 0)
  assert.equal(creationStatusLabel('completed'), '已完成')
  assert.equal(creationStatusLabel('new-status'), '创作中')
})

test('每条新消息的幂等键均唯一', () => {
  const ids = Array.from({ length: 50 }, () => creationMessageId())
  assert.equal(new Set(ids).size, ids.length)
  assert.ok(ids.every((id) => typeof id === 'string' && id.length > 10))
})

test('初次默认不强制3D，首句国风与后续自然语言画风均可由后端决策', () => {
  const first = buildCreationSubmission({
    content: '  用国风水墨做节气视频  ',
    selectedTemplateId: 'anim_3d',
  })
  assert.equal(first.content, '用国风水墨做节气视频')
  assert.equal(first.templateId, undefined)
  assert.equal(JSON.stringify(first).includes('templateId'), false)
  const defaultStyle = buildCreationSubmission({
    content: '做一条月亮科普',
    selectedTemplateId: 'anim_3d',
  })
  assert.equal(defaultStyle.templateId, undefined)
  const followup = buildCreationSubmission({
    content: '改成国风水墨',
    sessionId: '12',
    selectedTemplateId: 'anim_3d',
  })
  assert.equal(followup.templateId, undefined)
  assert.equal(JSON.stringify(followup).includes('templateId'), false)
  const explicit = buildCreationSubmission({
    content: '旁白再活泼一点',
    sessionId: '12',
    selectedTemplateId: 'ink_guofeng',
    explicitTemplateId: 'ink_guofeng',
  })
  assert.equal(explicit.templateId, 'ink_guofeng')
  assert.equal(
    buildCreationSubmission({ content: ' \n ', selectedTemplateId: 'anim_3d' }),
    null,
  )
})

test('非默认预选和明确选择3D仍作为用户风格选择提交', () => {
  const preselected = buildCreationSubmission({
    content: '做一条月亮科普',
    selectedTemplateId: 'ink_guofeng',
  })
  assert.equal(preselected.templateId, 'ink_guofeng')
  const explicitDefault = buildCreationSubmission({
    content: '用国风水墨做节气视频',
    selectedTemplateId: 'anim_3d',
    explicitTemplateId: 'anim_3d',
  })
  assert.equal(explicitDefault.templateId, 'anim_3d')
  const explicitFollowup = buildCreationSubmission({
    content: '换个画风',
    sessionId: '12',
    selectedTemplateId: 'anim_3d',
    explicitTemplateId: 'anim_3d',
  })
  assert.equal(explicitFollowup.templateId, 'anim_3d')
})

test('失败重发保留幂等键，编辑需求、显式风格或会话后创建新消息', () => {
  const options = {
    content: '让故事更温暖',
    sessionId: '4',
    selectedTemplateId: 'anim_3d',
  }
  const previous = buildCreationSubmission(options)
  assert.equal(
    buildCreationSubmission({ ...options, previous }).id,
    previous.id,
  )
  assert.equal(
    buildCreationSubmission({
      ...options,
      selectedTemplateId: 'soft_anime',
      previous,
    }).id,
    previous.id,
  )
  assert.notEqual(
    buildCreationSubmission({ ...options, content: '让故事更有趣', previous })
      .id,
    previous.id,
  )
  assert.notEqual(
    buildCreationSubmission({
      ...options,
      explicitTemplateId: 'soft_anime',
      previous,
    }).id,
    previous.id,
  )
  assert.notEqual(
    buildCreationSubmission({ ...options, sessionId: '5', previous }).id,
    previous.id,
  )
})
