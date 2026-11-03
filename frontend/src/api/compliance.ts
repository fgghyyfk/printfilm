/** 广告法合规扫描 API：科普项目 / 漫剧分集 */

import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'

/** 命中等级：block 极限词（明确禁止），warn 需资质/检测报告的宣称 */
export type ComplianceLevel = 'block' | 'warn'

/** 单个命中词 */
export type ComplianceHit = {
  word: string
  category: string
  level: ComplianceLevel
}

/** 科普项目命中位置字段 */
export type KepuComplianceField = 'title' | 'narration' | 'overlay_title' | 'overlay_subtitle'

/** 科普项目单条命中（项目标题或某镜某字段） */
export type KepuComplianceItem = {
  scope: 'project' | 'shot'
  shot_id: number | null
  index: number | null
  field: KepuComplianceField
  hits: ComplianceHit[]
}

/** 漫剧分镜单条命中 */
export type DramaComplianceItem = {
  fragment_id: number
  index: number
  hits: ComplianceHit[]
}

/** 扫描结果通用结构 */
export type ComplianceReport<T> = {
  mode: 'replace' | 'report'
  auto_replace: boolean
  count: number
  items: T[]
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
