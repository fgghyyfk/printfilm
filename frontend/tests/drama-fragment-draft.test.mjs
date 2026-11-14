import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createSerialSaver,
  fragmentDraftSignature,
  mergeServerFragmentsIntoDraft,
} from '../src/lib/dramaFragmentDraft.ts'

const frag = (over = {}) => ({
  id: 1, episode_id: 9, sort_order: 0, content: '原稿', cover: '', video: '', duration_sec: 8, asset_ids: [], params: {}, ...over,
})

test('成片和生成状态变化不算未保存修改', () => {
  const before = fragmentDraftSignature([frag()])
  const after = fragmentDraftSignature([frag({
    video: '/v.mp4', cover: '/c.png', params: { generation: { status: 'done' }, video_versions: [1], lastFrameUrl: '/l.png' },
  })])
  assert.equal(before, after)
})

test('内容、时长、引用、用户设置和分镜增删都算未保存修改', () => {
  const base = fragmentDraftSignature([frag()])
  for (const edited of [
    [frag({ content: '新稿' })],
    [frag({ duration_sec: 5 })],
    [frag({ asset_ids: [3] })],
    [frag({ params: { videoOptions: { model_id: 'x' } } })],
    [],
    [frag(), frag({ id: 0, content: '新增分镜' })],
  ]) {
    assert.notEqual(fragmentDraftSignature(edited), base)
  }
})

test('未落库的空白占位不算修改', () => {
  assert.equal(fragmentDraftSignature([frag({ id: 0, content: '  ' })]), fragmentDraftSignature([]))
})

test('params 键顺序不影响比较', () => {
  assert.equal(
    fragmentDraftSignature([frag({ params: { a: 1, b: 2 } })]),
    fragmentDraftSignature([frag({ params: { b: 2, a: 1 } })]),
  )
})

test('合并时保留草稿内容，并取服务端成片和运行态', () => {
  const draft = [frag({ content: '没保存的稿', params: { mine: 1, generation: { status: 'running' } } })]
  const server = [frag({ content: '库里的旧稿', video: '/v.mp4', cover: '/c.png', params: { generation: { status: 'done' }, video_versions: [1] } })]
  const [merged] = mergeServerFragmentsIntoDraft(draft, server)
  assert.equal(merged.content, '没保存的稿')
  assert.equal(merged.video, '/v.mp4')
  assert.equal(merged.cover, '/c.png')
  assert.deepEqual(merged.params, { mine: 1, generation: { status: 'done' }, video_versions: [1] })
})

test('合并保留未落库的新分镜，追加服务端新增，不恢复已删除', () => {
  const draft = [frag({ id: 1 }), frag({ id: 0, content: '新增' })]
  const server = [frag({ id: 1 }), frag({ id: 2, content: '别处新增' }), frag({ id: 3, content: '本页已删' })]
  const merged = mergeServerFragmentsIntoDraft(draft, server, new Set([3]))
  assert.deepEqual(merged.map((f) => f.id), [1, 0, 2])
})

test('串行保存同时只发一个请求，并且最后落库的是最新内容', async () => {
  const sent = []
  let inFlight = 0
  let maxInFlight = 0
  const saver = createSerialSaver(async (value) => {
    inFlight += 1
    maxInFlight = Math.max(maxInFlight, inFlight)
    await new Promise((resolve) => setTimeout(resolve, 5))
    sent.push(value)
    inFlight -= 1
  })
  await Promise.all([saver.save('A'), saver.save('B'), saver.save('C')])
  assert.equal(maxInFlight, 1)
  assert.equal(sent.at(-1), 'C')
  assert.ok(!sent.includes('B'), '中间内容被更新的内容取代，不必单独发送')
})

test('串行保存失败后还能继续保存', async () => {
  let fail = true
  const sent = []
  const saver = createSerialSaver(async (value) => {
    if (fail) {
      fail = false
      throw new Error('网络错误')
    }
    sent.push(value)
  })
  await assert.rejects(saver.save('A'))
  await saver.save('B')
  assert.deepEqual(sent, ['B'])
})
