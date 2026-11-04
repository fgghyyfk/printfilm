import { loadSource } from './bundleModule.mjs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
const { createFragmentAutosave } = await loadSource('../src/lib/dramaFragmentAutosave.ts')
const { duplicateEditableFragment } = await loadSource('../src/lib/dramaFragmentEditing.ts')

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const row = (id, content, sort_order = 0) => ({ id, content, sort_order, episode_id: 3, video: '', cover: '', params: {}, asset_ids: [], duration_sec: 8 })
function setup(persist) {
  const changes = [], states = []
  const editor = createFragmentAutosave({ persist, onRows: rows => changes.push(rows), onSaved() {}, onState: state => states.push(state), onError() {}, delay: 60000 })
  return { editor, changes, states }
}

test('保存A在途输入B，重排/复制的新镜只给原始draft赋ID，flush等待B也保存', async () => {
  const wait = deferred(), calls = []
  const { editor } = setup(async (rows, snapshot) => {
    calls.push({ rows: structuredClone(rows), snapshot })
    if (calls.length === 1) return wait.promise
    return { revision: 2, fragments: rows.map((r, index) => ({ ...r, id: r.id || 20, sort_order: index })) }
  })
  editor.load({ revision: 0, fragments: [] })
  editor.edit([row(0, 'A')])
  const flush = editor.flush()
  const original = editor.rows()[0]
  editor.edit([{ ...duplicateEditableFragment(original), content: 'copy-B' }, { ...original, content: 'B' }])
  wait.resolve({ revision: 1, fragments: [row(10, 'A')] })
  await flush
  assert.equal(calls.length, 2)
  assert.equal(calls[1].snapshot.revision, 1)
  assert.deepEqual(calls[1].rows.map(r => [r.id, r.content]), [[0, 'copy-B'], [10, 'B']])
  assert.deepEqual(editor.rows().map(r => [r.id, r.content]), [[20, 'copy-B'], [10, 'B']])
  assert.equal(editor.state().dirty, false)
  editor.dispose()
})

test('A中新镜在B删除时，A返回ID成为下一保存的显式删除', async () => {
  const wait = deferred(), calls = []
  const { editor } = setup(async (rows, snapshot) => {
    calls.push(snapshot)
    if (calls.length === 1) return wait.promise
    return { revision: 2, fragments: rows }
  })
  editor.load({ revision: 0, fragments: [row(1, 'keep')] })
  editor.edit(rows => [...rows, row(0, 'A', 1)])
  const flush = editor.flush()
  editor.edit(rows => rows.slice(0, 1))
  wait.resolve({ revision: 1, fragments: [row(1, 'keep'), row(10, 'A', 1)] })
  await flush
  assert.deepEqual(calls[1].deletedIds, [10])
  assert.equal(editor.rows().length, 1)
  editor.dispose()
})

test('setting等待中增删改只提交最新稿；runtime不脏，409保稿并停止自动重试', async () => {
  const calls = []
  const { editor } = setup(async (rows, snapshot) => {
    calls.push({ rows, snapshot })
    throw Object.assign(new Error('内容已在别处更新，请刷新'), { status: 409 })
  })
  editor.load({ revision: 7, fragments: [row(1, 'initial')] })
  editor.runtime(rows => rows.map(r => ({ ...r, video: 'done.mp4' })))
  assert.equal(editor.state().dirty, false)
  const release = editor.hold()
  editor.edit(rows => rows.map(r => ({ ...r, content: 'setting' })))
  const pending = editor.flush()
  editor.edit(rows => [{ ...rows[0], content: 'B' }, row(0, 'insert-B', 1)])
  assert.equal(calls.length, 0)
  release()
  await assert.rejects(pending, error => error.status === 409)
  assert.equal(calls[0].rows[0].content, 'B')
  assert.equal(calls[0].snapshot.revision, 7)
  assert.equal(editor.state().dirty, true)
  assert.equal(editor.state().conflict, true)
  await assert.rejects(editor.flush(), /本地稿已保留/)
  assert.equal(calls.length, 1)
  assert.equal(editor.rows()[0].content, 'B')
  editor.dispose()
})

test('切集后迟到保存不得发布旧内容或错误', async () => {
  const wait = deferred()
  const { editor, changes } = setup(() => wait.promise)
  editor.load({ revision: 0, fragments: [row(1, 'initial')] })
  editor.edit(rows => rows.map(r => ({ ...r, content: 'A' })))
  const saving = editor.flush()
  const count = changes.length
  editor.dispose()
  wait.resolve({ revision: 1, fragments: [row(1, 'A')] })
  await assert.rejects(saving, /分集已切换/)
  assert.equal(changes.length, count)
})

test('防抖合并输入且运行态轮询不会启动新保存', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let calls = 0
  const { editor } = setup(async rows => ({ revision: ++calls, fragments: rows }))
  editor.load({ revision: 0, fragments: [row(1, 'initial')] })
  editor.edit(rows => rows.map(r => ({ ...r, content: 'A' })))
  editor.edit(rows => rows.map(r => ({ ...r, content: 'B' })))
  t.mock.timers.tick(60000)
  await editor.flush()
  assert.equal(calls, 1)
  assert.equal(editor.rows()[0].content, 'B')
  editor.runtime(rows => rows.map(r => ({ ...r, video: 'done.mp4' })))
  t.mock.timers.tick(60000)
  assert.equal(calls, 1)
  editor.dispose()
})
