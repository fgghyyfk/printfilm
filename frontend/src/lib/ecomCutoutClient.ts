/** 浏览器内抠图的页面侧封装：懒建后台线程（整页共用一个），一次抠一张，回传进度。 */
import type { CutoutEvent } from '../workers/ecomCutout.worker'

/** 抠图进度：download 首次下载模型（ratio 0~1），load 加载模型，run 计算中 */
export type CutoutProgress = { stage: 'download' | 'load' | 'run'; ratio?: number }

let worker: Worker | null = null
let seq = 0
let queue: Promise<unknown> = Promise.resolve()

/** 当前浏览器能不能在本地抠图（需要后台线程、离屏画布、WebAssembly） */
export function canCutoutInBrowser(): boolean {
  return typeof Worker !== 'undefined' && typeof OffscreenCanvas !== 'undefined' && typeof WebAssembly !== 'undefined'
}

// 懒建后台线程
function getWorker(): Worker {
  worker ??= new Worker(new URL('../workers/ecomCutout.worker.ts', import.meta.url), { type: 'module' })
  return worker
}

/**
 * 把一张商品照片抠成透明底 PNG。多次调用自动排队（同一时间只跑一张，控制内存）。
 * @param file 原图
 * @param onProgress 进度回调
 * @returns 透明底 PNG
 */
export function cutoutImage(file: Blob, onProgress?: (p: CutoutProgress) => void): Promise<Blob> {
  const run = () => new Promise<Blob>((resolve, reject) => {
    const id = ++seq
    const w = getWorker()
    const onMessage = (e: MessageEvent<CutoutEvent>) => {
      const msg = e.data
      if (msg.id !== id) return
      if (msg.type === 'progress') { onProgress?.({ stage: msg.stage, ratio: msg.ratio }); return }
      w.removeEventListener('message', onMessage)
      w.removeEventListener('error', onError)
      if (msg.type === 'done') resolve(msg.blob)
      else reject(new Error(msg.message))
    }
    // 线程整体崩溃（多半是内存不够）：丢掉线程，下次重建
    const onError = () => {
      w.removeEventListener('message', onMessage)
      w.removeEventListener('error', onError)
      w.terminate()
      worker = null
      reject(new Error('这台设备抠图失败（可能内存不足），请换用电脑浏览器，或直接上传透明底 PNG'))
    }
    w.addEventListener('message', onMessage)
    w.addEventListener('error', onError)
    w.postMessage({ id, file })
  })
  const next = queue.then(run, run)
  queue = next.catch(() => undefined)
  return next
}
