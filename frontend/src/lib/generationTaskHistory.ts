import type { ToolRunRecord } from '../api/tools.ts'

/** TaskRun 与旧工具记录的数字 ID 独立，列表、下载和轮询必须带来源。 */
export function generationRecordKey(record: ToolRunRecord): string {
  return record.record_key || `${record.source || 'tool'}:${record.id}`
}

export function generationDetailPath(record: Pick<ToolRunRecord, 'id' | 'source'>): string {
  return record.source === 'task' ? `/api/tasks/generations/${record.id}` : `/api/tools/runs/${record.id}`
}

/** 完成时间不是结算凭据；费用被隐藏时不能将 null 当成零元。 */
export function generationBillingState(record: ToolRunRecord): 'hidden' | 'pending' | 'settled' | 'refunded' | 'free' | 'not_charged' {
  if (record.billing_charged_fen === null) return 'hidden'
  if (record.billing_status === 'refunded') return 'refunded'
  if (record.billing_status === 'settled') return 'settled'
  if (record.billing_status === 'skipped') return 'free'
  if (['reserved', 'frozen', 'pending'].includes(record.billing_status || '')) return 'pending'
  if ((record.billing_charged_fen || 0) > 0) return 'settled'
  return ['queued', 'running', 'pending'].includes(record.status) ? 'pending' : 'not_charged'
}
