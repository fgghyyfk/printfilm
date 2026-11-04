/** 广告法合规扫描 API：科普项目 / 漫剧分集 */

import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'
import { undoReplacement } from '../lib/complianceChanges'

/** 命中等级：block 极限词（明确禁止），warn 需资质/检测报告的宣称 */
export type ComplianceLevel = 'block' | 'warn'

/** 单个命中词 */
export type ComplianceHit = {
  word: string
  category: string
  level: ComplianceLevel
  start: number
  end: number
  /** null 无安全自动建议；空字符串表示明确删除。 */
  suggestion: string | null
}

/** 科普项目命中位置字段 */
export type KepuComplianceField = 'title' | 'narration' | 'overlay_title' | 'overlay_subtitle' | 'avatar_title2' | 'segment_script' | 'video_prompt' | 'highlights'

/** 科普项目单条命中（项目标题或某镜某字段） */
export type KepuComplianceItem = {
  scope: 'project' | 'shot'
  shot_id: number | null
  index: number | null
  field: KepuComplianceField
  text: string
  text_hash: string
  hits: ComplianceHit[]
}

/** 单次真实替换：区间均为Unicode码点，current区间随撤销更新。 */
export type ComplianceChange = {
  id: string
  old: string
  new: string
  original_start: number
  original_end: number
  start: number
  end: number
  current_start: number
  current_end: number
  undone: boolean
}

/** 数据库保存的生成原稿与差异，不含临时当前坐标。 */
export type ComplianceAudit = Record<string, {
  original: string
  result: string
  changes: Omit<ComplianceChange, 'current_start' | 'current_end'>[]
}>

/** 生成字段的历史替换及当前文本；matches_current由服务端校验。 */
export type ComplianceReplacementItem = {
  scope: 'project' | 'shot'
  shot_id: number | null
  index: number | null
  field: KepuComplianceField
  original: string
  text: string
  matches_current: boolean
  changes: ComplianceChange[]
}

/** 漫剧分镜单条命中 */
export type DramaComplianceItem = {
  fragment_id: number
  index: number
  text: string
  text_hash: string
  hits: ComplianceHit[]
}

/** 扫描结果通用结构 */
export type ComplianceReport<T> = {
  mode: 'replace' | 'report'
  auto_replace: boolean
  count: number
  items: T[]
  replacements?: ComplianceReplacementItem[]
}

export type KepuComplianceReport = ComplianceReport<KepuComplianceItem>
export type DramaComplianceReport = ComplianceReport<DramaComplianceItem>

// 合规提示是辅助信息：错误只抛普通 Error，不触发全局计费/登录弹窗
const request = createJsonRequester((_response, detail) => {
  throw new Error(apiErrorMessage(detail, '合规检查失败'))
})

// 扫描科普项目标题与各镜文案
export function getProjectCompliance(projectId: number): Promise<KepuComplianceReport> {
  return request<KepuComplianceReport>(`/api/projects/${projectId}/compliance`)
}

// 扫描漫剧单集各分镜台词
export function getEpisodeCompliance(episodeId: number): Promise<DramaComplianceReport> {
  return request<DramaComplianceReport>(`/api/drama/episodes/${episodeId}/compliance`)
}

/** 撤销一条真实生成替换，客户端不提交审计原稿。 */
export function undoProjectReplacement(projectId: number, row: ComplianceReplacementItem, change: ComplianceChange): Promise<void> {
  return undoReplacement(projectId, row, change, (path, body) => request(path, { method: 'PATCH', body: JSON.stringify(body) }))
}


/** 仅提交已扫描的字段/位置/原稿哈希；服务端锁内重新检查，不接收替换后整篇文案。 */
export async function applyProjectSuggestion(projectId: number, row: KepuComplianceItem, hit: ComplianceHit): Promise<void> {
  const path = row.scope === 'project' ? `/api/projects/${projectId}` : `/api/projects/${projectId}/shots/${row.shot_id}`
  await request(path, { method: 'PATCH', body: JSON.stringify({ compliance_apply: {
    field: row.field, text_hash: row.text_hash, start: hit.start, end: hit.end, word: hit.word, suggestion: hit.suggestion,
  } }) })
}
