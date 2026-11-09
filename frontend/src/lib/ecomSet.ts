/** 一图出套图纯函数：步骤判定、状态汇总、轮询判定、价格格式化、方案编辑辅助 */

import type {
  DaihuoRatio,
  EcomSet,
  EcomSetCopyPatch,
  EcomSetItem,
  EcomSetPlanItemPatch,
  EcomSetStatus,
} from '../api/ecomSet'

/** 带货视频可选画幅（第一个为默认） */
export const DAIHUO_RATIOS: DaihuoRatio[] = ['9:16', '16:9', '1:1', '4:3']

/** 页面四步：表单 → 方案 → 生成中 → 完成 */
export type EcomSetStep = 'form' | 'plan' | 'generating' | 'done'

/** 文案字数上限（与后端约定一致） */
export const ECOM_COPY_LIMITS = { title: 10, subtitle: 16, bullet: 12, bullets: 3, en: 40 } as const

/** 某张图的主标题字数上限：日化封面版式会自动换行，放宽到 16 字（与后端 title_limit 一致） */
export function titleLimitOf(slot: string): number {
  return slot === 'goods_cover' ? 16 : ECOM_COPY_LIMITS.title
}

/** 生成中轮询间隔 */
export const ECOM_POLL_MS = 3000

/** 商品图上传限制 */
export const ECOM_UPLOAD_ACCEPT = 'image/png,image/jpeg,image/webp'
export const ECOM_UPLOAD_MAX_BYTES = 10 * 1024 * 1024

const TERMINAL_SET: EcomSetStatus[] = ['done', 'partial', 'failed']

// 分 → ¥x.xx
export function formatFen(fen: number | null | undefined): string {
  const value = Number.isFinite(fen) ? Number(fen) : 0
  return `¥${(value / 100).toFixed(2)}`
}

// 套图是否处于终态（不再轮询）
export function isTerminalSetStatus(status: EcomSetStatus | string | undefined): boolean {
  return TERMINAL_SET.includes(status as EcomSetStatus)
}

// 单张是否仍在排队/生成
export function isItemBusy(item: Pick<EcomSetItem, 'status'>): boolean {
  return item.status === 'queued' || item.status === 'running'
}

// 是否需要继续轮询：套图生成中或仍有单张在跑
export function needsPolling(set: EcomSet | null | undefined): boolean {
  if (!set) return false
  if (set.status === 'generating') return true
  return set.items.some(isItemBusy)
}

// 根据套图状态决定当前步骤
export function resolveEcomStep(set: EcomSet | null | undefined): EcomSetStep {
  if (!set) return 'form'
  if (set.status === 'planned') return 'plan'
  if (set.status === 'generating') return 'generating'
  return 'done'
}

/** 进度计数 */
export type EcomSetProgress = { total: number; succeeded: number; failed: number; busy: number }

// 统计各状态张数
export function countEcomItems(items: EcomSetItem[]): EcomSetProgress {
  const progress = { total: items.length, succeeded: 0, failed: 0, busy: 0 }
  for (const item of items) {
    if (item.status === 'succeeded') progress.succeeded += 1
    else if (item.status === 'failed') progress.failed += 1
    else if (isItemBusy(item)) progress.busy += 1
  }
  return progress
}

// 是否可下载 zip：非生成中且至少一张成图
export function canDownloadSet(set: EcomSet): boolean {
  return set.status !== 'generating' && set.items.some((item) => Boolean(item.final_url))
}

// 是否可生成带货视频：非生成中且至少一张成功
export function canMakeDaihuoVideo(set: EcomSet | null | undefined): boolean {
  if (!set || set.status === 'generating' || set.status === 'planned') return false
  return set.items.some((item) => item.status === 'succeeded')
}

// Stepper 当前步：已建带货视频项目时进入第⑤步
export function daihuoStepIndex(set: EcomSet | null | undefined): number {
  if (set?.project_id) return 4
  return { form: 0, plan: 1, generating: 2, done: 3 }[resolveEcomStep(set)]
}

// 是否展示文案编辑（layout=none 为无字图）
export function hasCopy(item: Pick<EcomSetItem, 'layout'>): boolean {
  return item.layout !== 'none'
}

// 是否可改文案：有文案版式且已有无字底图
export function canEditCopy(item: EcomSetItem): boolean {
  return hasCopy(item) && Boolean(item.base_url) && !isItemBusy(item)
}

// 去空格、去空行、截断到最多 3 条
export function normalizeBullets(bullets: string[]): string[] {
  return bullets
    .map((b) => b.trim())
    .filter(Boolean)
    .slice(0, ECOM_COPY_LIMITS.bullets)
}

// 编辑用：补足到 3 个输入框
export function padBullets(bullets: string[]): string[] {
  const list = bullets.slice(0, ECOM_COPY_LIMITS.bullets)
  while (list.length < ECOM_COPY_LIMITS.bullets) list.push('')
  return list
}

// 将编辑稿转为 PUT /plan 的请求体
export function toPlanPatch(items: EcomSetItem[]): EcomSetPlanItemPatch[] {
  return items.map((item) => ({
    idx: item.idx,
    scene: item.scene.trim(),
    title: hasCopy(item) ? item.title.trim() : '',
    subtitle: hasCopy(item) ? item.subtitle.trim() : '',
    bullets: hasCopy(item) ? normalizeBullets(item.bullets) : [],
    en: hasCopy(item) ? (item.en || '').trim() : '',
  }))
}

// 将编辑稿转为 PUT copy 的请求体
export function toCopyPatch(copy: EcomSetCopyPatch): EcomSetCopyPatch {
  return {
    title: copy.title.trim(),
    subtitle: copy.subtitle.trim(),
    bullets: normalizeBullets(copy.bullets),
    en: (copy.en || '').trim(),
  }
}

// 方案是否被改动（比较可编辑字段）
export function isPlanDirty(original: EcomSetItem[], draft: EcomSetItem[]): boolean {
  const a = JSON.stringify(toPlanPatch(original))
  const b = JSON.stringify(toPlanPatch(draft))
  return a !== b
}

// 套图封面：优先第一张成图，否则商品原图
export function ecomSetCover(set: EcomSet): string {
  return set.items.find((item) => item.final_url)?.final_url || set.product_image_url || ''
}

// 生成全部的预估总价：以后端只计收费张数的 total_fen 为准，全免费时就是 0
export function ecomSetTotalFen(set: EcomSet): number {
  return set.total_fen
}

// 上传或生成模特后选中新加入的那位；默认模特排在列表首位，不能取第一项
export function newlyAddedModelId(before: { id: number }[], after: { id: number }[]): number | null {
  const known = new Set(before.map((item) => item.id))
  return (after.find((item) => !known.has(item.id)) ?? after[0])?.id ?? null
}

// 商品颜色款增删后，按原图地址找回当前颜色的新序号；原颜色已删除时回到第一个
export function reconcileColorIndex(
  previous: { image_url: string }[],
  index: number,
  next: { image_url: string }[],
): number {
  const found = next.findIndex((color) => color.image_url === previous[index]?.image_url)
  return found >= 0 ? found : 0
}

/** 套图表单实际支持的品类；服饰、首饰、包袋只能从电商图（/ecom）选商品出图 */
const ECOM_FORM_CATEGORIES = ['general', 'scarf']

// 过滤出套图表单能处理的品类选项
export function ecomFormCategories<T extends { id: string }>(options: T[] | undefined): T[] {
  return (options || []).filter((option) => ECOM_FORM_CATEGORIES.includes(option.id))
}

// 重新规划的去向：从商品库建的或表单不支持的品类回电商图，并带上原商品与颜色款；其余（返回 null）回本页表单
export function ecomReplanPath(set: Pick<EcomSet, 'category' | 'styled'>): string | null {
  const productId = set.styled?.product_id
  if (productId) return `/ecom?product=${productId}&color=${set.styled?.color_index ?? 0}`
  return ECOM_FORM_CATEGORIES.includes(set.category || 'general') ? null : '/ecom'
}

// CSS aspect-ratio 值：'3:4' → '3 / 4'
export function ratioToCss(ratio: string): string {
  const [w, h] = ratio.split(':').map((n) => Number(n))
  if (!w || !h) return '1 / 1'
  return `${w} / ${h}`
}

/** 上传校验失败原因（对应 i18n key） */
export type EcomUploadError = 'type' | 'size' | null

// 校验商品图格式与大小
export function validateProductFile(file: File): EcomUploadError {
  if (!ECOM_UPLOAD_ACCEPT.split(',').includes(file.type)) return 'type'
  if (file.size > ECOM_UPLOAD_MAX_BYTES) return 'size'
  return null
}

// 从 Content-Disposition 解析文件名（兼容 filename*=UTF-8''）
export function parseDispositionFilename(header: string | null | undefined): string {
  if (!header) return ''
  const star = header.match(/filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''))
    } catch {
      /* 解码失败时回退普通 filename */
    }
  }
  const plain = header.match(/filename\s*=\s*"?([^";]+)"?/)
  return plain?.[1]?.trim() || ''
}

// 默认 zip 文件名
export function defaultZipName(set: Pick<EcomSet, 'id' | 'name'>): string {
  const cleaned = Array.from(set.name || '')
    .map((ch) => (ch.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(ch) ? '_' : ch))
    .join('')
  const base = cleaned.trim().slice(0, 40)
  return `${base || '套图'}_${set.id}.zip`
}

// 解析 URL 中的 id 参数
export function parseSetId(raw: string | null): number | null {
  if (!raw) return null
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
}
