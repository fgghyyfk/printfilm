import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createFragmentEditSnapshot, markFragmentDeleted, fragmentSaveBody, duplicateEditableFragment, mergeFragmentRuntime } from '../src/lib/dramaFragmentEditing.ts'

// 编辑快照仅由完整加载或成功保存推进，运行态轮询不能提供新的编辑版本。
const fragment = { id: 12, episode_id: 3, sort_order: 0, content: 'local', video: 'old', cover: 'old', params: { user_setting: 1, video_versions: ['old'], generation: { status: 'running' } }, asset_ids: [] }

test('显式删除只记录已有id，重复删除不重复且不会从缺席推断删除', () => {
  const snapshot = createFragmentEditSnapshot({ revision: 7 })
  markFragmentDeleted(snapshot, 12)
  markFragmentDeleted(snapshot, 12)
  markFragmentDeleted(snapshot, 0)
  assert.deepEqual(fragmentSaveBody(snapshot, []), { revision: 7, deleted_ids: [12], fragments: [] })
})

test('保存序列化保留编辑版本且拒绝带上服务端生成字段', () => {
  const snapshot = createFragmentEditSnapshot({ revision: 7 })
  const body = fragmentSaveBody(snapshot, [fragment])
  assert.equal(body.revision, 7)
  assert.deepEqual(body.deleted_ids, [])
  assert.deepEqual(body.fragments[0].params, { user_setting: 1 })
  assert.equal(body.fragments[0].video, undefined)
  assert.equal(body.fragments[0].cover, undefined)
})

test('复制分镜保留创作内容和设置，清空id、媒体与历史', () => {
  const result = duplicateEditableFragment(fragment)
  assert.equal(result.id, 0)
  assert.equal(result.content, 'local')
  assert.equal(result.video, '')
  assert.equal(result.cover, '')
  assert.deepEqual(result.params, { user_setting: 1 })
})

test('运行态刷新只合并同id生成字段，保留本地内容、删除及原编辑revision', () => {
  const snapshot = createFragmentEditSnapshot({ revision: 7 })
  markFragmentDeleted(snapshot, 13)
  const result = mergeFragmentRuntime([fragment], [{ ...fragment, content: 'remote', video: 'new', params: { user_setting: 2, generation: { status: 'done' }, lastFrameUrl: 'tail', video_versions: ['latest'] } }, { ...fragment, id: 13 }])
  assert.equal(snapshot.revision, 7)
  assert.deepEqual(snapshot.deletedIds, [13])
  assert.equal(result.length, 1)
  assert.equal(result[0].content, 'local')
  assert.equal(result[0].video, 'new')
  assert.equal(result[0].params.user_setting, 1)
  assert.equal(result[0].params.lastFrameUrl, 'tail')
  assert.deepEqual(result[0].params.video_versions, ['latest'])
})

for (const intervening of ['ordinary', 'setting']) {
  test(`迟到设置使用捕获的旧版本与删除集合，${intervening}保存后应409`, async () => {
    const { saveFragmentsAfterSetting } = await import('../src/lib/dramaFragmentEditing.ts')
    let server = { revision: 7, content: 'initial' }
    const source = { current: { revision: 7, deletedIds: [13] } }
    const original = source.current
    const requests = []
    let entered
    let release
    const paused = new Promise((resolve) => { entered = resolve })
    const resumed = new Promise((resolve) => { release = resolve })
    // 模拟服务端真实CAS契约；仅成功时推进页面当前快照。
    const persist = async (snapshot, content) => {
      const body = fragmentSaveBody(snapshot, [{ ...fragment, content }])
      requests.push(body)
      if (body.revision !== server.revision) {
        const error = new Error('内容已在别处更新，请刷新')
        error.status = 409
        throw error
      }
      server = { revision: server.revision + 1, content }
      source.current = createFragmentEditSnapshot(server)
      return server
    }
    const late = saveFragmentsAfterSetting(source, async () => { entered(); await resumed }, (snapshot) => persist(snapshot, 'late-setting'))
    await paused
    // 复制删除集合，不能把等待期间原对象上的后续删除顺带带入旧动作。
    original.deletedIds.push(99)
    if (intervening === 'ordinary') await persist(source.current, 'new-edit')
    else await saveFragmentsAfterSetting(source, async () => {}, (snapshot) => persist(snapshot, 'new-edit'))
    release()
    await assert.rejects(late, (error) => error.status === 409)
    assert.equal(server.content, 'new-edit')
    assert.equal(server.revision, 8)
    assert.equal(requests[1].revision, 7)
    assert.deepEqual(requests[1].deleted_ids, [13])
  })
}
