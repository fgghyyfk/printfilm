/**
 * 商品实拍图浏览器内抠图的纯计算部分（不依赖 DOM，便于单测）。
 * 模型：ISNet 通用版（半精度 ONNX，输入 1×3×1024×1024，输出 1×1×1024×1024 前景概率）。
 * 流程：缩到 1024 → 归一化 → 推理 → 遮罩收紧 → 只留商品主体 → 放大回原图并裁到商品范围。
 */

/** 模型地址（站点静态文件，首次下载后存浏览器缓存） */
export const CUTOUT_MODEL_URL = '/ecom-models/isnet-fp16.onnx'
/** 模型缓存名（换模型时改版本号，旧缓存自动作废） */
export const CUTOUT_CACHE = 'kaikou-cutout-v1'
/** 模型固定输入边长 */
export const CUTOUT_INPUT = 1024
/** 输出抠图最长边（原图更大时先缩） */
export const CUTOUT_MAX_SIDE = 2400

const MEAN = [0.485, 0.456, 0.406]
const LOW = 0.04
const HIGH = 0.96
const MIN_COVER = 0.01
const MAX_COVER = 0.97
/** 明显小于最大块的零碎（画面文字、旁边小道具）去掉；两支同款并排大小相近会都保留 */
const KEEP_RATIO = 0.6
const GRID = 256

/**
 * RGBA 像素（1024×1024）转模型输入：CHW，按全图最大值归一后减均值（与 rembg 的 ISNet 预处理一致）。
 * @param rgba 长度 n*n*4 的像素
 */
export function toInputTensor(rgba: Uint8ClampedArray, n = CUTOUT_INPUT): Float32Array {
  const plane = n * n
  let max = 1
  for (let i = 0; i < rgba.length; i += 4) {
    if (rgba[i] > max) max = rgba[i]
    if (rgba[i + 1] > max) max = rgba[i + 1]
    if (rgba[i + 2] > max) max = rgba[i + 2]
  }
  const out = new Float32Array(plane * 3)
  for (let p = 0; p < plane; p++) {
    for (let c = 0; c < 3; c++) out[c * plane + p] = rgba[p * 4 + c] / max - MEAN[c]
  }
  return out
}

/** 模型输出（任意实数）→ 0~255 遮罩：按最小 / 最大值拉伸，再收紧两端（背景残影归零、商品内部填实） */
export function toMask(pred: Float32Array): Uint8ClampedArray {
  let lo = Infinity
  let hi = -Infinity
  for (const v of pred) { if (v < lo) lo = v; if (v > hi) hi = v }
  const span = Math.max(1e-6, hi - lo)
  const out = new Uint8ClampedArray(pred.length)
  for (let i = 0; i < pred.length; i++) {
    const v = ((pred[i] - lo) / span - LOW) / (HIGH - LOW)
    out[i] = Math.round(Math.min(1, Math.max(0, v)) * 255)
  }
  return out
}

/** 四邻域连通块标号，返回 [标号图, 每块面积]（标号从 1 开始） */
export function components(bin: Uint8Array, w: number, h: number): [Int32Array, number[]] {
  const labels = new Int32Array(w * h)
  const sizes: number[] = []
  const stack: number[] = []
  for (let s = 0; s < bin.length; s++) {
    if (!bin[s] || labels[s]) continue
    const id = sizes.length + 1
    labels[s] = id
    stack.push(s)
    let count = 0
    while (stack.length) {
      const i = stack.pop() as number
      count++
      const x = i % w
      const y = (i - x) / w
      const next = [y > 0 ? i - w : -1, y < h - 1 ? i + w : -1, x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1]
      for (const j of next) if (j >= 0 && bin[j] && !labels[j]) { labels[j] = id; stack.push(j) }
    }
    sizes.push(count)
  }
  return [labels, sizes]
}

/** 只留商品主体：在 256 网格上找连通块，去掉明显小于最大块的零碎；原地修改 n×n 遮罩 */
export function keepMain(mask: Uint8ClampedArray, n = CUTOUT_INPUT): void {
  const step = n / GRID
  const bin = new Uint8Array(GRID * GRID)
  for (let gy = 0; gy < GRID; gy++) {
    for (let gx = 0; gx < GRID; gx++) {
      bin[gy * GRID + gx] = mask[Math.floor(gy * step + step / 2) * n + Math.floor(gx * step + step / 2)] > 127 ? 1 : 0
    }
  }
  const [labels, sizes] = components(bin, GRID, GRID)
  if (sizes.length <= 1) return
  const big = Math.max(...sizes)
  const keepIds = new Set(sizes.flatMap((s, i) => (s >= big * KEEP_RATIO ? [i + 1] : [])))
  // 保留区外扩 2 格，边缘半透明过渡不被误删
  const gate = new Uint8Array(GRID * GRID)
  for (let i = 0; i < labels.length; i++) {
    if (!keepIds.has(labels[i])) continue
    const x = i % GRID
    const y = (i - x) / GRID
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      const yy = y + dy
      const xx = x + dx
      if (yy >= 0 && yy < GRID && xx >= 0 && xx < GRID) gate[yy * GRID + xx] = 1
    }
  }
  for (let y = 0; y < n; y++) {
    const gy = Math.min(GRID - 1, Math.floor(y / step))
    for (let x = 0; x < n; x++) if (!gate[gy * GRID + Math.min(GRID - 1, Math.floor(x / step))]) mask[y * n + x] = 0
  }
}

/** 商品范围（遮罩坐标，含 2% 边距）；主体太小或铺满整张时抛中文错误 */
export function maskBox(mask: Uint8ClampedArray, n = CUTOUT_INPUT): { x0: number; y0: number; x1: number; y1: number } {
  let solid = 0
  let x0 = n
  let y0 = n
  let x1 = -1
  let y1 = -1
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] > 127) solid++
    if (mask[i] > 8) {
      const x = i % n
      const y = (i - x) / n
      if (x < x0) x0 = x
      if (x > x1) x1 = x
      if (y < y0) y0 = y
      if (y > y1) y1 = y
    }
  }
  const cover = solid / mask.length
  if (x1 < 0 || cover < MIN_COVER || cover > MAX_COVER) throw new Error('没有识别出商品主体，请换一张商品更清楚、背景更干净的照片')
  const pad = Math.round(n * 0.02)
  return { x0: Math.max(0, x0 - pad), y0: Math.max(0, y0 - pad), x1: Math.min(n, x1 + 1 + pad), y1: Math.min(n, y1 + 1 + pad) }
}

/** 原图按最长边限制后的尺寸 */
export function fitSize(w: number, h: number, max = CUTOUT_MAX_SIDE): [number, number] {
  const k = Math.min(1, max / Math.max(w, h))
  return [Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k))]
}

/** 图片是否已经是透明底（超过 2% 的像素几乎全透明）：已抠好的图不用再抠 */
export function isTransparent(rgba: Uint8ClampedArray): boolean {
  let clear = 0
  const total = rgba.length / 4
  for (let i = 3; i < rgba.length; i += 4) if (rgba[i] < 16) clear++
  return clear / total > 0.02
}
