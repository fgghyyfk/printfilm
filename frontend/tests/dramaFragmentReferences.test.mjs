import assert from 'node:assert/strict'
import { test } from 'node:test'
import { orderedFragmentAssetIds, selectedFragmentAssetIds, fragmentCharacterLimitWarning } from '../src/lib/dramaFragmentReferences.ts'

test('角色优先级持久化后改变实际入选三人，重复正文不虚增警告', () => {
  const assets = [{ id: 1, type: 'scene' }, ...[11, 12, 13, 14].map(id => ({ id, type: 'character', name: `角色${id}` })),
    { id: 21, type: 'prop' }, { id: 22, type: 'prop' }]
  const fragment = { asset_ids: [1, 11, 12, 13, 14, 21, 22], content: '@asset:14 @asset:11 @asset:14', params: {} }
  assert.deepEqual(selectedFragmentAssetIds(fragment, assets), [1, 11, 12, 13, 21, 22])
  const saved = JSON.parse(JSON.stringify({ ...fragment, params: { reference_asset_order: [1, 14, 12, 13, 11, 21, 22] } }))
  assert.deepEqual(orderedFragmentAssetIds(saved), [1, 14, 12, 13, 11, 21, 22])
  assert.deepEqual(selectedFragmentAssetIds(saved, assets), [1, 14, 12, 13, 21, 22])
  assert.match(fragmentCharacterLimitWarning(saved, assets), /4 个角色.*角色11/)
  assert.equal(saved.content, fragment.content)
})
