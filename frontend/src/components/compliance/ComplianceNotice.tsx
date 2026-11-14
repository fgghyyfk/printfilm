import { useEffect, useRef, useState } from 'react'
import type { ComplianceChange, ComplianceHit, ComplianceReplacementItem, ComplianceReport } from '../../api/compliance'
import { useI18n } from '../../i18n'
import './complianceNotice.css'
import GeneratedCopyChanges from './GeneratedCopyChanges'

/** 单条命中的展示信息：位置文案 + 可选定位动作 */
export type ComplianceEntryView = {
  label: string
  onLocate?: () => void
}

type Props<T extends { hits: ComplianceHit[] }> = {
  /** 拉取扫描结果（每次重新检查都会调用最新一份） */
  fetchReport: () => Promise<ComplianceReport<T>>
  /** 文案签名：变化即（防抖后）自动重新检查；空串表示暂不检查 */
  refreshKey: string
  /** 把一条命中描述成位置文案与定位动作 */
  describe: (item: T) => ComplianceEntryView
  applyDisabled?: boolean
  onApply?: (item: T, hit: ComplianceHit) => Promise<void>
  undoDisabled?: boolean
  onUndoReplacement?: (row: ComplianceReplacementItem, change: ComplianceChange) => Promise<void>
}

// 防抖时长：轮询/连续保存时避免重复请求
const DEBOUNCE_MS = 500

/** 广告法合规提示条：当前风险与真实生成历史分别展示，支持重新检查 */
export default function ComplianceNotice<T extends { hits: ComplianceHit[] }>({
  fetchReport,
  refreshKey,
  describe,
  applyDisabled,
  onApply,
  undoDisabled,
  onUndoReplacement,
}: Props<T>) {
  const { t , t: translateUi } = useI18n()
  /*
   * report 最近一次成功的扫描结果
   * open 是否展开明细
   * loading 是否正在检查
   */
  const [report, setReport] = useState<ComplianceReport<T> | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState('')
  const fetchRef = useRef(fetchReport)
  const seqRef = useRef(0)

  useEffect(() => {
    fetchRef.current = fetchReport
  }, [fetchReport])

  // 发起一次检查；只采纳最新一次请求的结果，失败静默
  async function runCheck() {
    const seq = ++seqRef.current
    setApplyError('')
    setLoading(true)
    try {
      const next = await fetchRef.current()
      if (seq === seqRef.current) setReport(next)
    } catch (err) {
      console.warn('[compliance] 合规检查失败', err)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }

  async function apply(item: T, hit: ComplianceHit) {
    if (!onApply) return
    setApplying(true)
    setApplyError('')
    try { await onApply(item, hit); await runCheck() }
    catch (err) { setApplyError(err instanceof Error ? err.message : translateUi("workflow.replacementFailedScanAgain")) }
    finally { setApplying(false) }
  }

  useEffect(() => {
    if (!refreshKey) return
    const timer = window.setTimeout(() => {
      void runCheck()
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
    // runCheck 通过 ref 读取最新 fetcher，只需随签名触发
  }, [refreshKey])

  if (!report || (report.count <= 0 && !report.replacements?.length)) return null

  // 有命中：自动替换模式提示「已替换、仍需确认」，否则提示命中数；无命中仅剩生成替换记录
  const headline = report.count > 0
    ? (report.auto_replace
      ? t('compliance.autoReplaced', { count: report.count })
      : t('compliance.found', { count: report.count }))
    : t('compliance.generatedHistory')

  return (
    <div className="pf-compliance" role="status">
      <div className="pf-compliance-head">
        <button
          type="button"
          className="pf-compliance-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="pf-compliance-icon" aria-hidden>
            !
          </span>
          <span className="pf-compliance-title">{headline}</span>
          <span className="pf-compliance-caret">{open ? t('compliance.collapse') : t('compliance.expand')}</span>
        </button>
        <button
          type="button"
          className="pf-btn pf-btn-ghost pf-btn-sm"
          disabled={loading}
          onClick={() => void runCheck()}
        >
          {loading ? t('compliance.checking') : t('compliance.recheck')}
        </button>
      </div>
      {open ? (
        <div className="pf-compliance-body">
          {applyError ? <p role="alert">{applyError}</p> : null}
          <ul className="pf-compliance-list">
            {report.items.map((item, i) => {
              const view = describe(item)
              return (
                <li key={i} className="pf-compliance-row">
                  {view.onLocate ? (
                    <button
                      type="button"
                      className="pf-compliance-where is-link"
                      title={t('compliance.locateHint')}
                      onClick={view.onLocate}
                    >
                      {view.label}
                    </button>
                  ) : (
                    <span className="pf-compliance-where">{view.label}</span>
                  )}
                  <span className="pf-compliance-hits">
                    {item.hits.map((hit) => (
                      <span
                        key={`${hit.start}-${hit.end}-${hit.word}-${hit.level}`}
                        className={`pf-compliance-tag is-${hit.level === 'block' ? 'block' : 'warn'}`}
                        title={hit.level === 'block' ? t('compliance.levelBlock') : t('compliance.levelWarn')}
                      >
                        {hit.word}
                        <small>{hit.category}</small>
                        {hit.suggestion != null ? <span className="pf-compliance-suggestion">{translateUi("workflow.suggestion", { v1: hit.suggestion ? translateUi("workflow.replaceWith", { v1: String(hit.suggestion) }) : translateUi("workflow.removeThisWording") })}{onApply ? <button type="button" className="pf-btn pf-btn-ghost pf-btn-sm" disabled={applyDisabled || applying || loading}
                            onClick={() => void apply(item, hit)}>{translateUi("workflow.replaceThisOccurrence")}</button> : null}
                        </span> : null}
                      </span>
                    ))}
                  </span>
                </li>
              )
            })}
          </ul>
          {report.count > 0 ? <p className="pf-compliance-note">{t('compliance.note')}</p> : null}
          {report.replacements?.length ? <GeneratedCopyChanges
            rows={report.replacements}
            disabled={undoDisabled}
            onUndo={onUndoReplacement ? async (row, change) => {
              await onUndoReplacement(row, change)
              await runCheck()
            } : undefined}
          /> : null}
        </div>
      ) : null}
    </div>
  )
}
