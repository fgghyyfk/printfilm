/**
 * 商品实拍图抠图后台线程：在用户浏览器里跑 ISNet（优先 WebGPU，不支持时用 WASM），不占服务器资源。
 * 收到 { id, file } → 依次回传 progress 事件 → 最后回传透明底 PNG（Blob）或中文错误。
 */
import * as ort from 'onnxruntime-web/webgpu'
import {
  CUTOUT_CACHE, CUTOUT_INPUT, CUTOUT_MODEL_URL, fitSize, isTransparent, keepMain, maskBox, toInputTensor, toMask,
} from '../lib/ecomCutout'

/** 发给页面的消息 */
export type CutoutEvent =
  | { id: number; type: 'progress'; stage: 'download' | 'load' | 'run'; ratio?: number }
  | { id: number; type: 'done'; blob: Blob; width: number; height: number; backend: string }
  | { id: number; type: 'error'; message: string }

const ctx = self as unknown as { onmessage: ((e: MessageEvent) => void) | null; postMessage: (m: CutoutEvent) => void }

ort.env.wasm.numThreads = globalThis.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 2) : 1

let session: Promise<{ sess: ort.InferenceSession; backend: string }> | null = null

// 下载模型（带进度），存进浏览器缓存，下次直接读
async function fetchModel(onRatio: (r: number) => void): Promise<ArrayBuffer> {
  const cache = 'caches' in globalThis ? await caches.open(CUTOUT_CACHE).catch(() => null) : null
  const hit = await cache?.match(CUTOUT_MODEL_URL)
  if (hit) return hit.arrayBuffer()
  const res = await fetch(CUTOUT_MODEL_URL)
  if (!res.ok || !res.body) throw new Error('抠图模型下载失败，请稍后再试')
  const total = Number(res.headers.get('content-length')) || 0
  const reader = res.body.getReader()
  const parts: Uint8Array[] = []
  let got = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value)
    got += value.length
    if (total) onRatio(got / total)
  }
  const buf = new Uint8Array(got)
  let at = 0
  for (const p of parts) { buf.set(p, at); at += p.length }
  await cache?.put(CUTOUT_MODEL_URL, new Response(buf, { headers: { 'content-type': 'application/octet-stream' } })).catch(() => undefined)
  return buf.buffer
}

// 建推理会话（整个线程只建一次）：先试 WebGPU，失败退回 WASM
function getSession(id: number) {
  session ??= (async () => {
    const model = await fetchModel((ratio) => ctx.postMessage({ id, type: 'progress', stage: 'download', ratio }))
    ctx.postMessage({ id, type: 'progress', stage: 'load' })
    const gpu = 'gpu' in navigator
    if (gpu) {
      try {
        return { sess: await ort.InferenceSession.create(model, { executionProviders: ['webgpu'], graphOptimizationLevel: 'all' }), backend: 'webgpu' }
      } catch { /* 显卡不支持，退回 CPU */ }
    }
    return { sess: await ort.InferenceSession.create(model, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' }), backend: 'wasm' }
  })()
  session.catch(() => { session = null })
  return session
}

// 抠一张：缩图推理 → 遮罩处理 → 放大回原图 → 裁到商品范围 → PNG
async function cut(id: number, file: Blob): Promise<CutoutEvent> {
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error('图片无法识别，请换一张') })
  const [w, h] = fitSize(bitmap.width, bitmap.height)
  const n = CUTOUT_INPUT
  const small = new OffscreenCanvas(n, n).getContext('2d', { willReadFrequently: true }) as OffscreenCanvasRenderingContext2D
  small.imageSmoothingQuality = 'high'
  small.drawImage(bitmap, 0, 0, n, n)
  const pixels = small.getImageData(0, 0, n, n).data
  if (isTransparent(pixels)) { bitmap.close(); throw new Error('这张已经是透明底，不用再抠') }
  const input = new ort.Tensor('float32', toInputTensor(pixels), [1, 3, n, n])
  const { sess, backend } = await getSession(id)
  ctx.postMessage({ id, type: 'progress', stage: 'run' })
  const result = await sess.run({ [sess.inputNames[0]]: input })
  const pred = result[sess.outputNames[0]]
  const mask = toMask(await pred.getData() as Float32Array)
  for (const t of Object.values(result)) t.dispose()
  input.dispose()
  keepMain(mask)
  const box = maskBox(mask)
  // 遮罩放大到原图尺寸（画布双线性插值）
  const m = new OffscreenCanvas(n, n).getContext('2d') as OffscreenCanvasRenderingContext2D
  const md = m.createImageData(n, n)
  for (let i = 0; i < mask.length; i++) md.data[i * 4 + 3] = mask[i]
  m.putImageData(md, 0, 0)
  const full = new OffscreenCanvas(w, h).getContext('2d', { willReadFrequently: true }) as OffscreenCanvasRenderingContext2D
  full.imageSmoothingQuality = 'high'
  full.drawImage(m.canvas, 0, 0, w, h)
  const alpha = full.getImageData(0, 0, w, h).data
  full.clearRect(0, 0, w, h)
  full.drawImage(bitmap, 0, 0, w, h)
  bitmap.close()
  const px = full.getImageData(0, 0, w, h)
  for (let i = 3; i < px.data.length; i += 4) px.data[i] = alpha[i]
  const sx = w / n
  const sy = h / n
  const cx0 = Math.floor(box.x0 * sx)
  const cy0 = Math.floor(box.y0 * sy)
  const cw = Math.max(1, Math.ceil(box.x1 * sx) - cx0)
  const ch = Math.max(1, Math.ceil(box.y1 * sy) - cy0)
  const out = new OffscreenCanvas(cw, ch).getContext('2d') as OffscreenCanvasRenderingContext2D
  out.putImageData(px, -cx0, -cy0, cx0, cy0, cw, ch)
  const blob = await out.canvas.convertToBlob({ type: 'image/png' })
  return { id, type: 'done', blob, width: cw, height: ch, backend }
}

ctx.onmessage = (e: MessageEvent) => {
  const { id, file } = e.data as { id: number; file: Blob }
  void cut(id, file).then(ctx.postMessage, (err: unknown) => {
    const raw = err instanceof Error ? err.message : String(err)
    const message = /[一-龥]/.test(raw) ? raw : '这台设备抠图失败（可能内存不足），请换用电脑浏览器，或直接上传透明底 PNG'
    ctx.postMessage({ id, type: 'error', message })
  })
}
