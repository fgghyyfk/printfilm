import assert from 'node:assert/strict'
import test from 'node:test'
import { dramaProjectEntryPath, filterDramaProjectList } from '../src/lib/dramaWorkflow.ts'

test('canvas and drama lists stay separate including legacy canvas names', () => {
  const items = [
    { id: 1, title: '城市剧本', workflow: 'script', has_script: true, episode_count: 2 },
    { id: 2, title: '城市设计', workflow: 'canvas', asset_count: 3 },
    { id: 3, title: '自由画布旧作', asset_count: 0 },
    { id: 4, title: '自由画布题材', workflow: 'script', has_script: false },
  ]
  assert.deepEqual(filterDramaProjectList(items, { workflow: 'canvas' }).map(row => row.id), [2, 3])
  assert.deepEqual(filterDramaProjectList(items, { workflow: 'script' }).map(row => row.id), [1, 4])
  assert.equal(dramaProjectEntryPath(items[2]), '/drama/projects/3/canvas')
})

test('search also applies when filtering occupied or empty canvases', () => {
  const items = [
    { id: 1, title: '城市设计', workflow: 'canvas', asset_count: 3 },
    { id: 2, title: '山林设计', workflow: 'canvas', asset_count: 1 },
    { id: 3, title: '城市空白', workflow: 'canvas', asset_count: 0 },
  ]
  const find = (filter) => filterDramaProjectList(items, { workflow: 'canvas', filter, query: '城市' }).map(row => row.id)
  assert.deepEqual(find('running'), [1])
  assert.deepEqual(find('draft'), [3])
  assert.deepEqual(find('all'), [1, 3])
})
