import test from 'node:test'
import assert from 'node:assert/strict'
import { components, fitSize, keepMain, maskBox, toInputTensor, toMask } from '../src/lib/ecomCutout.ts'

const N = 64

/** 生成 N×N 遮罩，rects 内为 255 */
function mask(rects) {
  const m = new Uint8ClampedArray(N * N)
  for (const [x0, y0, x1, y1] of rects) for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) m[y * N + x] = 255
  return m
}

test('输入张量：CHW 排列，按最大值归一后减均值', () => {
  const rgba = new Uint8ClampedArray(2 * 2 * 4).fill(255)
  rgba[0] = 0
  const t = toInputTensor(rgba, 2)
  assert.equal(t.length, 12)
  assert.ok(Math.abs(t[0] - (0 - 0.485)) < 1e-6)
  assert.ok(Math.abs(t[4 + 1] - (1 - 0.456)) < 1e-6)
})

test('遮罩：拉伸到 0~255 并收紧两端', () => {
  const m = toMask(new Float32Array([-3, -2.9, 0, 2.9, 3]))
  assert.equal(m[0], 0)
  assert.equal(m[1], 0)
  assert.equal(m[4], 255)
  assert.ok(m[2] > 100 && m[2] < 155)
})

test('连通块：分开的两块各自计数', () => {
  const bin = new Uint8Array([1, 1, 0, 0, 0, 1, 0, 0, 1])
  const [, sizes] = components(bin, 3, 3)
  assert.deepEqual(sizes, [2, 2])
})

test('只留主体：去掉画面文字等零碎，两支同款都保留', () => {
  const m = mask([[20, 8, 44, 60], [2, 2, 8, 4]])
  keepMain(m, N)
  assert.equal(m[30 * N + 30], 255)
  assert.equal(m[3 * N + 4], 0)
  const pair = mask([[4, 8, 24, 60], [40, 8, 60, 60]])
  keepMain(pair, N)
  assert.equal(pair[30 * N + 10], 255)
  assert.equal(pair[30 * N + 50], 255)
})

test('商品范围：带边距；空遮罩或铺满整张报中文错误', () => {
  const box = maskBox(mask([[20, 8, 44, 60]]), N)
  assert.ok(box.x0 < 20 && box.x1 > 44 && box.y0 < 8 && box.y1 <= N)
  assert.throws(() => maskBox(new Uint8ClampedArray(N * N), N), /商品主体/)
  assert.throws(() => maskBox(new Uint8ClampedArray(N * N).fill(255), N), /商品主体/)
})

test('原图限制最长边', () => {
  assert.deepEqual(fitSize(4800, 2400), [2400, 1200])
  assert.deepEqual(fitSize(800, 600), [800, 600])
})

test('已是透明底的图识别出来，不再重复抠', async () => {
  const { isTransparent } = await import('../src/lib/ecomCutout.ts')
  const opaque = new Uint8ClampedArray(100 * 4).fill(255)
  assert.equal(isTransparent(opaque), false)
  const cut = opaque.slice()
  for (let i = 0; i < 30; i++) cut[i * 4 + 3] = 0
  assert.equal(isTransparent(cut), true)
})
