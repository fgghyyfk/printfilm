/** 生成替换明细的码点高亮与窄撤销请求。 */
import type { ComplianceChange, ComplianceHit, ComplianceReplacementItem } from '../api/compliance'

/** 按服务端Unicode码点区间切分，避免JS UTF16截断emoji。 */
export function replacementPreview(row: ComplianceReplacementItem, change: ComplianceChange) {
  const chars = Array.from(row.text)
  return {
    before: chars.slice(0, change.current_start).join(''),
    match: chars.slice(change.current_start, change.current_end).join(''),
    after: chars.slice(change.current_end).join(''),
  }
}

/** 手改后或已撤销的记录仅供复核原稿。 */
export function canUndoReplacement(row: ComplianceReplacementItem, change: ComplianceChange): boolean {
  return row.matches_current && !change.undone
}

/** 只提交服务端记录标识，沿用项目/分镜PATCH及其鉴权、素材失效规则。 */
export async function undoReplacement(
  projectId: number,
  row: ComplianceReplacementItem,
  change: ComplianceChange,
  patch: (path: string, body: { compliance_undo: { field: string; change_id: string } }) => Promise<unknown>,
): Promise<void> {
  if (!canUndoReplacement(row, change)) throw new Error('文案已编辑或该条已撤销，请对照原稿手动修改')
  const path = row.scope === 'project'
    ? `/api/projects/${projectId}`
    : `/api/projects/${projectId}/shots/${row.shot_id}`
  await patch(path, { compliance_undo: { field: row.field, change_id: change.id } })
}


/** 用户显式应用当前命中；先验证完整原稿和码点片段，绝不猜重复词的位置。 */
export function applyComplianceSuggestion(current: string, scanned: string, hit: ComplianceHit): string {
  const chars = Array.from(current)
  if (hit.suggestion == null || current !== scanned || !Number.isInteger(hit.start) || !Number.isInteger(hit.end)
      || hit.start < 0 || hit.end <= hit.start || chars.slice(hit.start, hit.end).join('') !== hit.word) {
    throw new Error('文案已变化，请重新检查后再替换')
  }
  return chars.slice(0, hit.start).join('') + hit.suggestion + chars.slice(hit.end).join('')
}
