import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import ts from 'typescript'

/** 转译单个源文件并在沙箱执行真实逻辑；依赖由用例提供。 */
async function loadModule(path, dependencies = {}) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText
  const sandbox = {
    exports: {}, Math, Number, String, Set, Map, Array, JSON, RegExp, setTimeout, clearTimeout,
    require(name) {
      if (name in dependencies) return dependencies[name]
      return new Proxy({}, { get: () => () => undefined })
    },
  }
  vm.runInNewContext(compiled, sandbox)
  return sandbox.exports
}

/** 最小 React 钩子替身：驱动 useCanvasAutoSave 的一次渲染和卸载。 */
function hookRuntime() {
  const refs = []
  const effects = []
  let refIndex = 0
  const react = {
    useRef(value) {
      if (refs.length <= refIndex) refs.push({ current: value })
      return refs[refIndex++]
    },
    useCallback: (fn) => fn,
    useEffect(effect, deps) { effects.push({ effect, deps }) },
  }
  return {
    react,
    render(hook, args) {
      refIndex = 0
      effects.length = 0
      const result = hook(args)
      return { result, cleanups: effects.map(({ effect }) => effect()).filter(Boolean) }
    },
  }
}

/** 加载真实的自动保存钩子，保存请求由用例记录。 */
async function loadAutoSave(saved) {
  const runtime = hookRuntime()
  const module = await loadModule('../src/pages/drama/canvas/useCanvasAutoSave.ts', {
    react: runtime.react,
    '../../../api/drama': { dramaApi: { saveCanvas: async (body) => { saved.push(body) } } },
    './canvasTypes': { CANVAS_AUTO_SAVE_MS: 60_000 },
  })
  return { runtime, useCanvasAutoSave: module.useCanvasAutoSave }
}

const node = { id: 'asset-1', type: 'asset', position: { x: 9, y: 9 }, data: { kind: 'image', label: '图' } }
const baseArgs = { projectId: 1, nodes: [node], edges: [], enabled: true, onSaved() {}, onError() {} }

test('第 6 条：防抖期内离开画布，卸载时立刻保存最新布局', async () => {
  const saved = []
  const { runtime, useCanvasAutoSave } = await loadAutoSave(saved)
  const { cleanups } = runtime.render(useCanvasAutoSave, { ...baseArgs, dirty: true })
  for (const cleanup of cleanups) cleanup()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(saved.length, 1)
  assert.equal(saved[0].project_id, 1)
  assert.equal(saved[0].nodes[0].position.x, 9)
})

test('第 6 条：没有未保存改动时离开，不发保存请求', async () => {
  const saved = []
  const { runtime, useCanvasAutoSave } = await loadAutoSave(saved)
  const { cleanups } = runtime.render(useCanvasAutoSave, { ...baseArgs, dirty: false })
  for (const cleanup of cleanups) cleanup()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(saved.length, 0)
})

test('第 5 条：新节点名称避开已从画布移除但仍保留的资产', async () => {
  const { nextCanvasNodeLabelInProject } = await loadModule('../src/pages/drama/canvas/canvasTypes.ts')
  const retained = [{ type: 'image', name: '新图片' }, { type: 'character', name: '新图片 2' }]
  assert.equal(nextCanvasNodeLabelInProject('image', [], retained), '新图片 2')
  const onCanvas = [{ data: { kind: 'image', label: '新图片 2' } }]
  assert.equal(nextCanvasNodeLabelInProject('image', onCanvas, retained), '新图片 3')
  assert.equal(nextCanvasNodeLabelInProject('scene', [], retained), '新场景')
})

test('第 1 条：音色资产在画布上显示为音频节点，道具仍是图片卡', async () => {
  const { dramaAssetTypeToKind } = await loadModule('../src/pages/drama/canvas/assetsToCanvasNodes.ts')
  assert.equal(dramaAssetTypeToKind('voice'), 'audio')
  assert.equal(dramaAssetTypeToKind('audio'), 'audio')
  assert.equal(dramaAssetTypeToKind('prop'), 'image')
})

test('第 7 条：手写分镜时长按 Seedance 4–30 秒，不再收紧到 15 秒', async () => {
  const { resolveFragmentDurationSec } = await loadModule('../src/pages/drama/dramaEpisodeEditUtils.ts')
  assert.equal(resolveFragmentDurationSec('@duration:12\n一\n@duration:12\n二', 8), 24)
  assert.equal(resolveFragmentDurationSec('@duration:20\n一\n@duration:20\n二', 8), 30)
  assert.equal(resolveFragmentDurationSec('@duration:2\n一', 8), 4)
})
