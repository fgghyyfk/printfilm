import { useState } from 'react'
import type { ComplianceChange, ComplianceReplacementItem } from '../../api/compliance'
import { canUndoReplacement, replacementPreview } from '../../lib/complianceChanges'
import { useI18n } from '../../i18n'

/** 真实生成替换历史：码点高亮、原稿复核与单条撤销。 */
export default function GeneratedCopyChanges({ rows, disabled, onUndo }: {
  rows: ComplianceReplacementItem[]
  disabled?: boolean
  onUndo?: (row: ComplianceReplacementItem, change: ComplianceChange) => Promise<void>
}) {
  const { t } = useI18n()
  // pending 撤销请求在途；error 失败信息保留在原记录旁，避免静默失败。
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  // 由服务端校验并保存撤销，完成后父层重新读取历史与项目。
  async function undo(row: ComplianceReplacementItem, change: ComplianceChange) {
    if (!onUndo) return
    setPending(true)
    setError('')
    try {
      await onUndo(row, change)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('compliance.undoFailed'))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="pf-compliance-history">
      <p>{t('compliance.generatedNote')}</p>
      {error ? <p role="alert">{error}</p> : null}
      {rows.map((row) => (
        <section key={`${row.scope}-${row.shot_id}-${row.field}`}>
          <strong>{row.scope === 'project'
            ? t(`compliance.fields.${row.field}`)
            : t('compliance.where.shot', { index: row.index ?? '?', field: t(`compliance.fields.${row.field}`) })}</strong>
          {!row.matches_current ? <p>{t('compliance.edited')}</p> : null}
          <ol>
            {row.changes.map((change) => {
              const preview = replacementPreview(row, change)
              return (
                <li key={change.id}>
                  <span><mark>{change.old || t('compliance.empty')}</mark> → <mark>{change.new || t('compliance.empty')}</mark></span>
                  {onUndo ? <button type="button" className="pf-btn pf-btn-ghost pf-btn-sm"
                    disabled={disabled || pending || !canUndoReplacement(row, change)}
                    onClick={() => void undo(row, change)}>
                    {t(change.undone ? 'compliance.undone' : 'compliance.undo')}
                  </button> : null}
                  {row.matches_current ? <pre className="pf-compliance-preview">{preview.before}<mark>{preview.match || t('compliance.empty')}</mark>{preview.after}</pre> : null}
                </li>
              )
            })}
          </ol>
          <details><summary>{t('compliance.original')}</summary><pre className="pf-compliance-preview">{row.original}</pre></details>
        </section>
      ))}
    </div>
  )
}
