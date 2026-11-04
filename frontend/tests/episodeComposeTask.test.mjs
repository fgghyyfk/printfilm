import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rolldown } from 'rolldown'

/** 只替代编码兼容库和API边界，执行真实浏览器回退编排。 */
async function composeModule(states) {
  const hooks = { posts: 0, gets: 0, downloads: [], states }
  const key = `compose_${Math.random().toString(36).slice(2)}`
  globalThis[key] = hooks
  const ref = `globalThis[${JSON.stringify(key)}]`
  const stubs = {
    '/api/drama': `export const resolveDramaMediaUrl=x=>x; export const dramaApi={composeEpisode:async()=>{${ref}.posts++;return {task_id:123}}}`,
    '/api/tasks': `export const tasksApi={get:async id=>{if(id!==123)throw Error('错误任务');${ref}.gets++;return ${ref}.states.shift()}}`,
    '/clientDownload': `export async function fetchMediaBlob(url){${ref}.downloads.push(url);return new Blob(['video'])}`,
    '/canvasNodeMedia': 'export const sanitizeMediaBasename=x=>x',
    'mp4cat': 'export const isMp4=()=>true;export const mp4Compat=()=>({ok:false});export const concatMp4=()=>new Uint8Array()',
  }
  const bundle = await rolldown({
    input: 'src/lib/composeEpisodeVideoClient.ts',
    plugins: [{
      name: 'compose-boundaries',
      resolveId(id) {
        const match = Object.keys(stubs).find((suffix) => id === suffix || id.endsWith(suffix))
        return match ? `\0${match}` : null
      },
      load(id) { return id.startsWith('\0') ? stubs[id.slice(1)] : null },
    }],
  })
  const generated = await bundle.generate({ format: 'esm', codeSplitting: false })
  const code = generated.output.find((item) => item.type === 'chunk').code
  await bundle.close()
  return { hooks, key, module: await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`) }
}

test('不兼容视频只提交一次服务端任务，完成后下载任务结果', async () => {
  const { hooks, key, module } = await composeModule([
    { status: 'pending' }, { status: 'succeeded', result_payload: { video_url: '/static/composed.mp4' } },
  ])
  const setTimeoutBefore = globalThis.setTimeout
  globalThis.setTimeout = (callback) => setTimeoutBefore(callback, 0)
  try {
    const result = await module.composeEpisodeVideoClient([
      { id: 1, url: '/static/a.mp4', label: '一' }, { id: 2, url: '/static/b.mp4', label: '二' },
    ], undefined, { episodeId: 7 })
    assert.equal(hooks.posts, 1)
    assert.equal(hooks.gets, 2)
    assert.equal(hooks.downloads.at(-1), '/static/composed.mp4')
    assert.equal(await result.text(), 'video')
  } finally {
    globalThis.setTimeout = setTimeoutBefore
    delete globalThis[key]
  }
})

test('服务端合成失败停止轮询并保留中文原因', async () => {
  const { hooks, key, module } = await composeModule([{ status: 'failed', error_message: '视频合成失败，请稍后重新拼接' }])
  try {
    await assert.rejects(module.composeEpisodeVideoClient([
      { id: 1, url: '/static/a.mp4', label: '一' }, { id: 2, url: '/static/b.mp4', label: '二' },
    ], undefined, { episodeId: 7 }), /视频合成失败/)
    assert.equal(hooks.posts, 1)
    assert.equal(hooks.gets, 1)
    assert.equal(hooks.downloads.length, 2)
  } finally { delete globalThis[key] }
})
