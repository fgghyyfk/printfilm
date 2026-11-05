import { AlertCircle, Download, Loader2, Pencil, RefreshCw } from 'lucide-react'
import type { EcomSet, EcomSetItem } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { useI18n } from '../../i18n'
import { canDownloadSet, canEditCopy, countEcomItems, isItemBusy, ratioToCss } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import EcomSetWarnings from './EcomSetWarnings'

type Props = {
  set: EcomSet
  /** 正在提交操作的单张 idx */
  busyIdx: number | null
  downloading: boolean
  error: string
  onPreview: (item: EcomSetItem) => void
  onEditCopy: (item: EcomSetItem) => void
  onRegenerate: (item: EcomSetItem) => void
  onRetry: (item: EcomSetItem) => void
  onDownload: () => void
  onNew: () => void
}

/** 第③④步：成图网格，生成中显示占位，完成后可改文案 / 重新生成 / 打包下载 */
export default function EcomSetResults(props: Props) {
  const { set, busyIdx, downloading, error, onDownload, onNew } = props
  const { t } = useI18n()
  const progress = countEcomItems(set.items)
  const generating = set.status === 'generating'

  return (
    <section className="pf-ecom-section">
      <div className="pf-ecom-section-head">
        <div>
          <h2>{generating ? t('ecomSet.result.generatingTitle') : t('ecomSet.result.doneTitle')}</h2>
          <p className="pf-muted">
            {t('ecomSet.result.progress', { done: progress.succeeded, total: progress.total })}
            {progress.failed ? ` · ${t('ecomSet.result.failedCount', { count: progress.failed })}` : ''}
            {generating ? ` · ${t('ecomSet.result.waitHint')}` : ''}
          </p>
        </div>
        <div className="pf-ecom-actions">
          <Button variant="text" size="sm" onClick={onNew}>
            {t('ecomSet.result.newSet')}
          </Button>
          <Button variant="lime" size="sm" disabled={!canDownloadSet(set) || downloading} onClick={onDownload}>
            {downloading ? (
              <Loader2 size={14} className="pf-tool-spin" aria-hidden />
            ) : (
              <Download size={14} aria-hidden />
            )}
            {downloading ? t('ecomSet.result.downloading') : t('ecomSet.result.download')}
          </Button>
        </div>
      </div>

      <EcomSetWarnings warnings={set.warnings} />
      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}

      <div className="pf-ecom-grid" style={{ marginTop: error ? '0.75rem' : 0 }}>
        {set.items.map((item) => (
          <ResultCard key={item.idx} item={item} pending={busyIdx === item.idx} {...props} />
        ))}
      </div>
    </section>
  )
}

/** 单张成图卡片：图片区三态 + 操作按钮 */
function ResultCard({
  item,
  pending,
  onPreview,
  onEditCopy,
  onRegenerate,
  onRetry,
}: Props & { item: EcomSetItem; pending: boolean }) {
  const { t } = useI18n()
  const busy = isItemBusy(item)
  const image = item.final_url ? resolveToolMediaUrl(item.final_url) : ''

  return (
    <article className="pf-ecom-card">
      <div className="pf-ecom-card-head">
        <strong>
          {item.idx + 1}. {t(`ecomSet.enhanced.slots.${item.slot}.name`)}
        </strong>
        <span className="pf-badge">{item.ratio}</span>
      </div>
      <div className="pf-ecom-thumb" style={{ aspectRatio: ratioToCss(item.ratio) }}>
        {busy ? (
          <div className="pf-ecom-thumb-state">
            <Loader2 size={24} className="pf-tool-spin" aria-hidden />
            {item.status === 'queued' ? t('ecomSet.result.queued') : t('ecomSet.result.running')}
          </div>
        ) : item.status === 'failed' ? (
          <div className="pf-ecom-thumb-state is-error">
            <AlertCircle size={22} aria-hidden />
            <strong>{t('ecomSet.result.failed')}</strong>
            {item.error ? <span>{item.error}</span> : null}
          </div>
        ) : image ? (
          <button
            type="button"
            className="pf-ecom-thumb-btn"
            aria-label={t('ecomSet.result.preview')}
            onClick={() => onPreview(item)}
          >
            <img src={image} alt={t(`ecomSet.enhanced.slots.${item.slot}.name`)} loading="lazy" />
          </button>
        ) : (
          <div className="pf-ecom-thumb-state">{t('ecomSet.result.noImage')}</div>
        )}
      </div>
      {!busy ? (
        <div className="pf-ecom-card-actions">
          {item.status === 'failed' ? (
            <Button variant="outline" size="sm" disabled={pending} onClick={() => onRetry(item)}>
              <RefreshCw size={14} aria-hidden />
              {t('ecomSet.result.retry')}
            </Button>
          ) : (
            <>
              {canEditCopy(item) ? (
                <Button variant="outline" size="sm" disabled={pending} onClick={() => onEditCopy(item)}>
                  <Pencil size={14} aria-hidden />
                  {t('ecomSet.result.editCopy')}
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" disabled={pending} onClick={() => onRegenerate(item)}>
                <RefreshCw size={14} aria-hidden />
                {t('ecomSet.result.regenerate')}
              </Button>
            </>
          )}
        </div>
      ) : null}
    </article>
  )
}
