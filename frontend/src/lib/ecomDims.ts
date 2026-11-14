import type { EcomDims } from '../api/ecomProducts'

/** 不填实际尺寸的品类：丝巾按方巾尺寸、服饰按穿着 */
const NO_DIMS = new Set(['scarf', 'apparel'])

/** 该品类是否需要填实际尺寸 */
export function usesDims(category: string | undefined): boolean {
  return Boolean(category) && !NO_DIMS.has(category || '')
}

/** 有没有填任何一项尺寸 */
export function hasDims(d?: EcomDims | null): boolean {
  return Boolean(d && (d.h || d.w || d.d))
}

/** 「15×4×3cm」，没有尺寸返回空串 */
export function dimsShort(d?: EcomDims | null): string {
  const vals = (['h', 'w', 'd'] as const).map((k) => d?.[k]).filter((v): v is number => Boolean(v))
  return vals.length ? `${vals.join('×')}cm` : ''
}

/** 输入框的字符串 → 尺寸数值（非正数、非数字、超过 300 的丢弃） */
export function parseDim(value: string): number | undefined {
  const n = Number(value.trim())
  return Number.isFinite(n) && n > 0 && n <= 300 ? Math.round(n * 10) / 10 : undefined
}
