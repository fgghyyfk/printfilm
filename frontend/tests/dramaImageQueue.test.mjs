import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rolldown } from 'rolldown'

// 载入真实队列模块，仅替换网络和跨页展示边界，不复制提交/轮询逻辑。
async function queueModule(api) {
  globalThis.__imageTestApi = api
  globalThis.__imageQueueUpdates = []
  const bundle = await rolldown({
    input: 'src/lib/dramaImageGenQueue.ts',
    plugins: [{
      name: 'controlled-image-boundaries',
      resolveId(source) {
        if (source === '../api/drama') return '\0image-api'
        if (source === './dramaGenQueue') return '\0image-unified'
      },
      load(id) {
        if (id === '\0image-api') return 'export const dramaApi = globalThis.__imageTestApi'
        if (id === '\0image-unified') return 'export const syncImageJobToUnified = input => globalThis.__imageQueueUpdates.push(input)'
      },
    }],
  })
  const { output } = await bundle.generate({ format: 'esm' })
  await bundle.close()
  return import(`data:text/javascript;base64,${Buffer.from(`${output[0].code}\n//# sourceURL=controlled-image-queue.js`).toString('base64')}#${Math.random()}`)
}

test('等待报价后别处已出图，服务端skip立即回显且不继续轮询或称新生成', async () => {
  const calls = []
  const existing = { id: 8, project_id: 7, url: '/static/existing.png', params: { generation: { status: 'done' } } }
  const queue = await queueModule({
    async listAssets() {
      calls.push('list')
      if (calls.length > 1) throw new Error('跳过后不应继续轮询')
      return [existing]
    },
    async generateImage(body) {
      calls.push(body)
      return { ok: true, queued: false, skipped: true, status: 'skipped', asset: existing }
    },
  })
  const submissions = []
  const actual = await queue.enqueueDramaImageGen({
    projectId: 7, assetId: 8, prompt: '角色', missingOnly: true,
    onSubmission: result => submissions.push(result),
  })
  assert.equal(actual, existing)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].missing_only, true)
  assert.deepEqual(submissions, [{ skipped: true, reused: false }])
  assert.match(globalThis.__imageQueueUpdates.at(-1).message, /跳过/)
})

test('接回服务端在途任务计为复用，报价后的选项仍为提交快照', async () => {
  const options = { model_id: 'selected-image', resolution: '2K', aspect_ratio: '3:4' }
  const submissions = []
  const generated = { id: 8, project_id: 7, url: '/static/generated.png', params: { generation: { status: 'done' } } }
  let reads = 0
  const queue = await queueModule({
    async listAssets() { reads += 1; return reads === 1 ? [{ id: 8, url: '' }] : [generated] },
    async generateImage(body) {
      assert.equal(body.model_id, 'selected-image')
      assert.equal(body.resolution, '2K')
      return { ok: true, queued: true, reused: true, task_id: 42, status: 'running' }
    },
  })
  const result = queue.enqueueDramaImageGen({ projectId: 7, assetId: 8, prompt: '角色', options,
    missingOnly: true, onSubmission: value => submissions.push(value) })
  options.model_id = 'later-change'
  assert.equal(await result, generated)
  assert.deepEqual(submissions, [{ skipped: false, reused: true }])
})
