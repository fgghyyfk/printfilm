import { loadSource } from './bundleModule.mjs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createMemoryRouter } from 'react-router-dom'
const { createFragmentAutosave } = await loadSource('../src/lib/dramaFragmentAutosave.ts')
const { flushFragmentNavigation } = await loadSource('../src/lib/fragmentNavigation.ts')
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

for (const action of ['PUSH', 'REPLACE', 'POP']) test(`${action}导航等待A后还要保存最新B`, async () => {
  const a = deferred(), calls = []
  const editor = createFragmentAutosave({ delay: 60000, persist: async rows => {
    calls.push(rows[0].content)
    if (calls.length === 1) await a.promise
    return { revision: calls.length, fragments: rows }
  }, onRows() {}, onSaved() {}, onState() {}, onError() {} })
  editor.load({ revision: 0, fragments: [{ id: 1, content: 'old' }] })
  const router = createMemoryRouter([{ path: '*', element: null }], { initialEntries: ['/previous', '/edit'], initialIndex: 1 })
  router.getBlocker('draft', () => editor.state().dirty || editor.state().saving)
  editor.edit([{ id: 1, content: 'A' }]); const saving = editor.flush()
  editor.edit([{ id: 1, content: 'B' }])
  void router.navigate(action === 'POP' ? -1 : '/next', { replace: action === 'REPLACE' })
  const blocker = router.getBlocker('draft', () => true)
  assert.equal(blocker.state, 'blocked')
  const transition = flushFragmentNavigation(editor, blocker, () => true)
  assert.equal(router.state.location.pathname, '/edit')
  a.resolve(); await saving; await transition
  assert.deepEqual(calls, ['A', 'B'])
  assert.equal(router.state.location.pathname, action === 'POP' ? '/previous' : '/next')
  editor.dispose(); router.dispose()
})

test('409拒绝导航并保留本地稿', async () => {
  const editor = createFragmentAutosave({ persist: async () => { throw Object.assign(new Error('conflict'), { status: 409 }) }, onRows() {}, onSaved() {}, onState() {}, onError() {}, delay: 60000 })
  editor.load({ revision: 0, fragments: [] }); editor.edit([{ id: 1, content: '草稿' }])
  const router = createMemoryRouter([{ path: '*', element: null }], { initialEntries: ['/edit'] })
  router.getBlocker('draft', () => true); void router.navigate('/next')
  await flushFragmentNavigation(editor, router.getBlocker('draft', () => true), () => true)
  assert.equal(router.state.location.pathname, '/edit')
  assert.equal(editor.rows()[0].content, '草稿')
  assert.equal(editor.state().conflict, true)
  editor.dispose(); router.dispose()
})

test('旧分集保存迟到完成不能放行或重置新导航', async () => {
  const wait = deferred()
  const editor = createFragmentAutosave({ persist: async rows => { await wait.promise; return { revision: 1, fragments: rows } }, onRows() {}, onSaved() {}, onState() {}, onError() {}, delay: 60000 })
  editor.load({ revision: 0, fragments: [] }); editor.edit([{ id: 1, content: '旧分集稿' }])
  let active = true, proceeded = 0, reset = 0
  const pending = flushFragmentNavigation(editor, { proceed() { proceeded++ }, reset() { reset++ } }, () => active)
  active = false; editor.dispose(); wait.resolve(); await pending
  assert.deepEqual([proceeded, reset], [0, 0])
})
