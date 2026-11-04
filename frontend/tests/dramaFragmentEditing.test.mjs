import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createFragmentEditSnapshot, fragmentSaveBody, duplicateEditableFragment, mergeFragmentRuntime } from '../src/lib/dramaFragmentEditing.ts'

// 编辑快照仅由完整加载或成功保存推进，运行态轮询不能提供新的编辑版本。
const fragment = { id: 12, episode_id: 3, sort_order: 0, content: 'local', video: 'old', cover: 'old', params: { user_setting: 1, video_versions: ['old'], generation: { status: 'running' } }, asset_ids: [] }

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
  snapshot.deletedIds.push(13)
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
