/** 广告法合规提示：位置文案与「何时需要重新检查」的签名（纯函数） */

import type { Project } from '../api'
import type { DramaComplianceItem, KepuComplianceItem } from '../api/compliance'
import type { DramaEpisode, DramaFragment } from '../api/drama'
import type { TFunction } from '../i18n'

// 科普命中位置：「标题」或「第 X 镜 · 旁白/标题/副标题」
export function kepuComplianceLabel(item: KepuComplianceItem, t: TFunction): string {
  if (item.scope === 'project') return t(`compliance.fields.${item.field}`)
  return t('compliance.where.shot', {
    index: item.index ?? '?',
    field: t(`compliance.fields.${item.field}`),
  })
}

// 漫剧命中位置：优先按 fragment_id 在当前列表中的位置编号（1 起），找不到再用接口 index
export function dramaComplianceLabel(
  item: DramaComplianceItem,
  fragments: DramaFragment[],
  t: TFunction,
): string {
  const pos = fragments.findIndex((f) => f.id === item.fragment_id)
  return t('compliance.where.fragment', { index: pos >= 0 ? pos + 1 : item.index })
}

// 科普项目文案签名：标题/旁白/小标题变化（保存、重新拆镜、轮询回写）即触发重新检查
export function projectComplianceKey(project: Project | null): string {
  if (!project) return ''
  const shots = (project.shots || []).map(
    (s) => `${s.id}\u0001${s.narration || ''}\u0001${s.overlay_title || ''}\u0001${s.overlay_subtitle || ''}\u0001${s.segment_script || ''}\u0001${s.video_prompt || ''}\u0001${s.highlights || ''}\u0001${JSON.stringify(s.compliance_changes || {})}`,
  )
  return `${project.id}\u0002${project.title || ''}\u0002${project.avatar_title2 || ''}\u0002${JSON.stringify(project.compliance_changes || {})}\u0002${shots.join('\u0003')}`
}

// 漫剧分集签名：仅取服务端已保存的分镜内容（本地未保存编辑不触发）
export function episodeComplianceKey(episode: DramaEpisode | null): string {
  if (!episode) return ''
  const frags = (episode.fragments || []).map((f) => `${f.id}\u0001${f.content || ''}`)
  return `${episode.id}\u0002${frags.join('\u0003')}`
}

// 科普镜头行的 DOM id，供合规提示点击定位
export function shotRowDomId(shotId: number): string {
  return `pf-shot-row-${shotId}`
}

// 平滑滚动到指定科普镜头行并短暂高亮
export function scrollToShotRow(shotId: number): void {
  const el = document.getElementById(shotRowDomId(shotId))
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('pf-compliance-flash')
  window.setTimeout(() => el.classList.remove('pf-compliance-flash'), 1600)
}
