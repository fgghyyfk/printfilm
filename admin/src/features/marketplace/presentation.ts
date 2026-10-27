const reviewStatuses: Record<string, string> = {
  draft: '草稿', pending: '待审核', processing: '正在生成短试看', preview_failed: '短试看生成失败', approved: '已通过', rejected: '已驳回',
}
const taskStatuses: Record<string, string> = {
  pending: '排队中', running: '处理中', processing: '处理中', done: '已完成', ready: '已就绪', failed: '处理失败', cancelled: '已取消', missing: '未生成', none: '未生成',
}
const domainStatuses: Record<string, Record<string, string>> = {
  works: reviewStatuses,
  series: reviewStatuses,
  recruits: { draft: '草稿', pending: '待审核', approved: '已通过', rejected: '已驳回' },
  previews: { pending: '排队中', running: '处理中', done: '已完成', failed: '处理失败', cancelled: '已取消' },
  jobs: { pending: '排队中', running: '处理中', done: '已完成', failed: '处理失败' },
  orders: { pending: '待支付', paid: '已支付', failed: '支付失败' },
  payouts: { pending: '待打款', paid: '已打款', failed: '打款失败' },
  reports: { pending: '待处理', resolved: '已处理' },
  entitlements: { active: '有效权益', inactive: '已停用' },
}

// 每种记录只提供服务端支持的状态筛选，避免把收益方式当作状态。
export function statusOptions(kind: string): [string, string][] {
  return Object.entries(domainStatuses[kind] || {})
}

export function statusLabel(kind: string, status: unknown): string {
  if (kind === 'ledger') return ({ pending: '待结算', settled: '已结算' } as Record<string, string>)[String(status)] || '结算状态待确认'
  return domainStatuses[kind]?.[String(status)] || '状态待确认'
}

export function recordStatus(kind: string, item: Record<string, unknown>): string {
  if (kind === 'entitlements') return item.active ? 'active' : 'inactive'
  if (kind === 'ledger') return String(item.settlement_status || 'pending')
  return String(item.audit_status || item.status || item.selection || '')
}

export function fieldStatusLabel(field: string, value: unknown): string | undefined {
  if (field === 'audit_status' || field === 'basic_status' || field === 'full_status') return reviewStatuses[String(value)]
  if (field === 'preview_status' || field === 'playback_status' || field === 'media_status') return taskStatuses[String(value)]
  if (field === 'settlement_status') return statusLabel('ledger', value)
  return undefined
}

export function recordDetailsTitle(kind: string): string {
  return ({
    works: '作品与版权信息', series: '短剧与版权信息', recruits: '招募需求详情', reports: '举报详情', previews: '短试看任务详情',
    jobs: '历史媒体任务详情', submissions: '历史投稿详情', orders: '订单详情', entitlements: '播放权益详情', ledger: '收益明细', payouts: '打款详情', events: '操作详情',
  } as Record<string, string>)[kind] || '记录详情'
}
