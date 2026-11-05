/* 电商工具路由与工作流分组，共用于工作台、历史和结果菜单。 */
export type EcomKind = 'set' | 'refine' | 'style' | 'buyer_show' | 'sku' | 'translate'
export const ECOM_PATHS: Record<EcomKind, string> = {
  set: '/daihuo', refine: '/ecom/refine', style: '/ecom/style',
  buyer_show: '/ecom/buyer-show', sku: '/ecom/sku', translate: '/ecom/translate',
}
export const ECOM_GROUPS: { key: string; tools: EcomKind[] }[] = [
  { key: 'prepare', tools: ['refine'] },
  { key: 'images', tools: ['set', 'style', 'buyer_show', 'sku'] },
  { key: 'global', tools: ['translate'] },
]

/** 按类型打开任务详情，套图与批量任务保持各自既有 URL。 */
export function ecomHistoryHref(kind: EcomKind, id: number): string {
  return `${ECOM_PATHS[kind]}?id=${id}`
}

/** 传递来源标识给目标工具，由服务端重新验证图片归属与成功状态。 */
export function nextStepHref(kind: 'set' | 'style' | 'sku', jobId: number, idx: number): string {
  return `${ECOM_PATHS[kind]}?source_job_id=${jobId}&source_item_idx=${idx}`
}

/** 成功且可预览的图片至少两张才可转成带货分镜。 */
export function canUseJobVideo(items: readonly { status: string; result_url: string | null }[]): boolean {
  return items.filter((item) => item.status === 'succeeded' && item.result_url).length >= 2
}
