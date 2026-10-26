import assert from 'node:assert/strict'
import test from 'node:test'
import { dramaProjectEntryPath, resolveDramaWorkflow } from '../src/lib/dramaWorkflow.ts'

test('canvas and drama lists stay separate including legacy canvas names', () => {
  const items = [
    { id: 1, title: '城市剧本', workflow: 'script', has_script: true, episode_count: 2 },
    { id: 2, title: '城市设计', workflow: 'canvas', asset_count: 3 },
    { id: 3, title: '自由画布旧作', asset_count: 0 },
    { id: 4, title: '自由画布题材', workflow: 'script', has_script: false },
  ]
  assert.deepEqual(items.map(resolveDramaWorkflow), ['script', 'canvas', 'canvas', 'script'])
  assert.equal(dramaProjectEntryPath(items[2]), '/drama/projects/3/canvas')
})

test('project entry keeps explicit workflow, legacy source and invalid IDs safe', () => {
  assert.equal(dramaProjectEntryPath({ id: 9, title: '自由画布题材', workflow: 'script' }), '/drama/projects/9')
  assert.equal(dramaProjectEntryPath({ id: 7, script: { source: '自由画布创作项目，稍后完善。' } }), '/drama/projects/7/canvas')
  assert.equal(dramaProjectEntryPath({ id: -1, workflow: 'canvas' }), '/drama')
})
