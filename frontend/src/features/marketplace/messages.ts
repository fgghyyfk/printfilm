/** 用户提示与接口错误码分开处理，不改变服务端状态和错误契约。 */
const businessMessages: Record<string, string> = {
  MARKETPLACE_STORAGE_UNAVAILABLE: '文件服务暂未配置或暂时不可用，请稍后重试或联系客服。',
  MARKETPLACE_PAYMENT_UNAVAILABLE: '支付服务暂不可用，请稍后重试。',
  MARKETPLACE_REVENUE_CONFIG_REQUIRED: '平台投放收益暂未开放，请联系客服确认。',
  MARKETPLACE_RIGHTS_REQUIRED: '请先确认版权信息：开口播作品签署授权函，其他平台作品提交版权证明。',
  MARKETPLACE_EPISODE_NOT_READY: '视频尚未准备好，请稍后刷新查看。',
  MARKETPLACE_ENTITLEMENT_REQUIRED: '请先购买该发布版本的在线播放权益。',
  MARKETPLACE_SOURCE_NOT_READY: '源成片尚未就绪，请完成视频生成后再同步。',
  MARKETPLACE_SOURCE_CHANGED: '源成片已更新，请刷新并重新选择后同步。',
  MARKETPLACE_TARGET_CHANGED: '市场作品已更新，请刷新后重新确认同步内容。',
  MARKETPLACE_SOURCE_INCOMPATIBLE: '分镜视频规格不一致，请统一画幅和分辨率后重新同步。',
  MARKETPLACE_SYNC_IN_PROGRESS: '成片正在同步，请等待同步完成后再保存或提交。',
  MARKETPLACE_SUBMISSIONS_DISABLED: '招募通过平台客服接单，请在招募详情中联系客服。',
}

const fieldNames: Record<string, string> = {
  title: '标题', description: '简介', category: '分类', tags: '标签', price_fen: '参考价',
  copyright_price_fen: '参考价', unlock_price_fen: '观众解锁价', share_bps: '平台抽成比例',
  license_scope: '拟授权用途及限制', scope: '授权范围', asset_id: '视频', cover_asset_id: '封面', source: '内容来源',
  certificate_number: '版权证书编号', certificate_asset_id: '版权证明图片', dispute_ack: '版权声明',
  episode_no: '集号', episodes: '分集', episode_ids: '所选分集', episode_id: '分集', free_episode_count: '免费集数',
  budget_fen: '单条预算', wanted_count: '征集数量', requirements: '交付要求', deadline_at: '截止时间',
  image_ids: '参考图片', evidence_ids: '证明材料', valid_until: '授权有效期', details: '版权信息',
  signer_name: '签署人姓名', strokes: '手写签名', accepted_terms: '授权条款', adult: '成年确认',
  read_all: '阅读确认', accept_key: '重点条款确认', template_version: '授权条款版本',
  sale_type: '收益方式', filename: '文件名', size_bytes: '文件大小', kind: '文件类型',
  statement: '举报说明', contact: '联系方式', version: '内容版本', series_version: '短剧版本',
  drama_project_id: '漫剧项目', project_id: '成片项目', series_id: '市场短剧', source_version: '源成片版本',
  idempotency_key: '本次操作编号', target_type: '咨询类型', target_id: '咨询内容',
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function marketplaceErrorCode(detail: unknown): string | undefined {
  const value = record(detail)
  if (typeof value?.code === 'string') return value.code
  const message = typeof detail === 'string' ? detail : typeof value?.message === 'string' ? value.message : ''
  return message.match(/^(MARKETPLACE_[A-Z_]+)(?=[:：\s]|$)/)?.[1]
}

function readableText(text: string): string | undefined {
  const cleaned = text.trim().replace(/^Value error,\s*/i, '')
  // 有中文的业务说明可以保留；纯错误码或底层英文交给统一回退提示。
  return /[\u3400-\u9fff]/u.test(cleaned) ? cleaned : undefined
}

function limitText(value: unknown, field: string): string {
  if (typeof value !== 'number' && typeof value !== 'string') return '允许的范围'
  if (field.endsWith('_fen') && Number.isFinite(Number(value))) return `¥${(Number(value) / 100).toFixed(2)}`
  return String(value)
}

function validationMessage(row: Record<string, unknown>, field: string): string {
  const type = row.type
  const ctx = record(row.ctx) ?? {}
  const raw = typeof row.msg === 'string' ? row.msg : ''
  const ownMessage = readableText(raw)
  if (ownMessage) return ownMessage
  switch (type) {
    case 'missing': return '这是必填项'
    case 'string_too_short': return `至少填写 ${ctx.min_length} 个字`
    case 'string_too_long': return `不能超过 ${ctx.max_length} 个字`
    case 'too_short': return `至少需要 ${ctx.min_length} 项`
    case 'too_long': return `最多允许 ${ctx.max_length} 项`
    case 'greater_than': return `须大于 ${limitText(ctx.gt, field)}`
    case 'greater_than_equal': return `不能小于 ${limitText(ctx.ge, field)}`
    case 'less_than': return `须小于 ${limitText(ctx.lt, field)}`
    case 'less_than_equal': return `不能超过 ${limitText(ctx.le, field)}`
    case 'int_parsing': case 'int_type': case 'int_from_float': return '请填写整数'
    case 'float_parsing': case 'float_type': case 'finite_number': return '请填写有效数字'
    case 'literal_error': return ctx.expected === 'True' ? '请勾选确认' : '请选择有效选项'
    case 'bool_parsing': case 'bool_type': return '请确认此选项'
    case 'datetime_parsing': case 'datetime_from_date_parsing': case 'timezone_aware': return '请填写有效的日期和时间'
    case 'extra_forbidden': return '此项不支持提交，请刷新页面后重试'
  }
  if (/field required/i.test(raw)) return '这是必填项'
  if (/at least 1 character/i.test(raw)) return '不能为空'
  if (/valid integer/i.test(raw)) return '请填写整数'
  if (/valid number/i.test(raw)) return '请填写有效数字'
  return '请检查填写内容'
}

function explainValidation(detail: unknown[]): string {
  const lines = detail.flatMap(value => {
    const row = record(value)
    if (!row) return []
    const parts = (Array.isArray(row.loc) ? row.loc : []).filter(part => !['body', 'query', 'path'].includes(String(part)))
    const field = String(parts.at(-1) ?? '')
    const location = parts.map(part => typeof part === 'number' ? `第 ${part + 1} 项` : fieldNames[String(part)] || '填写内容').join('的')
    const message = validationMessage(row, field)
    return [location ? `${location}：${message}` : message]
  })
  return [...new Set(lines)].join('；') || '请检查表单内容和必填字段'
}

export function explainMarketplaceError(detail: unknown, status?: number): string {
  if (Array.isArray(detail)) return explainValidation(detail)
  const value = record(detail)
  const raw = typeof detail === 'string' ? detail : typeof value?.message === 'string' ? value.message : ''
  const code = marketplaceErrorCode(detail)
  // 带具体原因的中文说明优先于通用错误码说明。
  const message = readableText(raw.replace(/^MARKETPLACE_[A-Z_]+\s*[:：]?\s*/, ''))
  if (message) return message
  if (code && businessMessages[code]) return businessMessages[code]
  if (status === 401) return '登录已过期，请重新登录后继续。'
  if (status === 403) return '当前账号没有执行此操作的权限。'
  if (status === 404) return '内容不存在或暂不可访问，请刷新后重试。'
  if (status === 409) return '内容状态已变化，请刷新后重新操作。'
  if (status === 413) return '文件超过允许大小，请选择较小的文件。'
  if (status === 422) return '请检查表单内容和必填字段。'
  if (status === 429) return '操作过于频繁，请稍后再试。'
  return '暂时无法完成操作，请稍后重试。'
}
