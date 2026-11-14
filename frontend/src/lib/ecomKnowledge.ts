/** 商品资料编辑：资料 ⇄ 「每行一条、｜分隔字段」文本互转（纯函数） */

import type { EcomKnowledge } from '../api/ecomProducts'

/** 可编辑的列表字段：key、标签、每行的字段（单字段为纯文本行）、示例 */
export const KNOWLEDGE_SECTIONS = [
  { key: 'selling_points', label: '卖点', fields: ['title', 'desc'], hint: '卖点｜说明，如：清爽不黏腻｜轻薄乳霜，很快吸收' },
  { key: 'variants', label: '款式 / 香型', fields: ['name', 'top', 'middle', 'base', 'desc'], hint: '款名｜前调｜中调｜后调｜描述' },
  { key: 'ingredients', label: '成分', fields: ['name', 'desc'], hint: '成分｜作用，如：乳木果油｜柔润肌肤' },
  { key: 'specs', label: '规格信息', fields: ['label', 'value'], hint: '项目｜内容，如：净含量｜60g' },
  { key: 'usage', label: '使用方法', fields: [], hint: '每行一步' },
  { key: 'scenarios', label: '适用场景', fields: [], hint: '每行一个' },
  { key: 'faq', label: '常见问题', fields: ['q', 'a'], hint: '问题｜回答' },
  { key: 'cautions', label: '注意事项', fields: [], hint: '每行一条' },
  { key: 'outline', label: '讲述顺序', fields: [], hint: '参考详情每屏在讲什么，规划卖点图时按这个顺序' },
  { key: 'angles', label: '打动角度（竞品）', fields: [], hint: '从竞品详情学到的角度，只学思路不抄原文' },
] as const

export type KnowledgeSectionKey = (typeof KNOWLEDGE_SECTIONS)[number]['key']
export type KnowledgeDraft = { summary: string; en_name: string; story: string } & Record<KnowledgeSectionKey, string>

const SEP = /[｜|]/

/** 资料 → 编辑稿 */
export function toKnowledgeDraft(k: Partial<EcomKnowledge> | null | undefined): KnowledgeDraft {
  const draft = { summary: k?.summary || '', en_name: k?.en_name || '', story: k?.story || '' } as KnowledgeDraft
  for (const s of KNOWLEDGE_SECTIONS) {
    const rows = ((k?.[s.key] as unknown[]) || [])
    draft[s.key] = rows.map((row) => {
      if (!s.fields.length) return String(row || '')
      const r = row as Record<string, string>
      const cells = s.fields.map((f) => (r[f] || '').trim())
      while (cells.length > 1 && !cells[cells.length - 1]) cells.pop()
      return cells.join('｜')
    }).join('\n')
  }
  return draft
}

/** 编辑稿 → 资料（空行丢弃） */
export function fromKnowledgeDraft(draft: KnowledgeDraft): Partial<EcomKnowledge> {
  const out: Record<string, unknown> = { summary: draft.summary.trim(), en_name: draft.en_name.trim(), story: draft.story.trim() }
  for (const s of KNOWLEDGE_SECTIONS) {
    const lines = (draft[s.key] || '').split('\n').map((l) => l.trim()).filter(Boolean)
    out[s.key] = s.fields.length
      ? lines.map((l) => {
        const cells = l.split(SEP).map((c) => c.trim())
        return Object.fromEntries(s.fields.map((f, i) => [f, cells[i] || '']))
      })
      : lines
  }
  return out as Partial<EcomKnowledge>
}

/** 资料里有多少条内容（用于摘要显示） */
export function knowledgeCount(k: Partial<EcomKnowledge> | null | undefined): number {
  if (!k) return 0
  return KNOWLEDGE_SECTIONS.reduce((n, s) => n + ((k[s.key] as unknown[]) || []).length, 0) + (k.story ? 1 : 0)
}
