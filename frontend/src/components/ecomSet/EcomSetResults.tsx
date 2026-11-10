import { useState, type ReactNode } from 'react'
import { AlertCircle, ChevronLeft, ChevronRight, Download, Loader2, Pencil, RefreshCw } from 'lucide-react'
import { reorderEcomSet, type EcomSet, type EcomSetItem } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { useI18n } from '../../i18n'
import { useGenerationText } from '../../lib/generationMedia'
import { canDownloadSet, canEditCopy, countEcomItems, isItemBusy, ratioToCss, swapOrder } from '../../lib/ecomSet'
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
  /** 额外操作（长图、同款换色等），放在下载按钮前 */
  extraActions?: ReactNode
  /** 调整顺序后回传新套图；不传则不显示左右移动按钮 */
  onReordered?: (set: EcomSet) => void
}

/** 第③④步：成图网格，生成中显示占位，完成后可改文案 / 重新生成 / 打包下载 */
export default function EcomSetResults(props: Props) {
  const { set, busyIdx, downloading, error, onDownload, onNew, extraActions } = props
  const { t } = useI18n()
  const text = useGenerationText()
  const sceneName = set.styled?.scene_name || ''
  const progress = countEcomItems(set.items)
  const generating = set.status === 'generating'
  /* 调整顺序：提交中的状态与错误 */
  const [moving, setMoving] = useState(false)
  const [moveError, setMoveError] = useState('')

  /** 左移 / 右移一张（生成中不可调） */
  const move = async (pos: number, dir: -1 | 1) => {
    const order = swapOrder(set.items, pos, dir)
    if (!order || !props.onReordered) return
    setMoving(true)
    setMoveError('')
    try {
      props.onReordered(await reorderEcomSet(set.id, order))
    } catch (e) {
      setMoveError(e instanceof Error ? e.message : text('调整顺序失败', 'Reorder failed'))
    } finally {
      setMoving(false)
    }
  }
  const canMove = Boolean(props.onReordered) && !generating && !moving

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
          {sceneName ? <p className="pf-muted">{text(`场景：${sceneName}`, `Scene: ${sceneName}`)}</p> : null}
        </div>
        <div className="pf-ecom-actions">
          <Button variant="text" size="sm" onClick={onNew}>
            {t('ecomSet.result.newSet')}
          </Button>
          {extraActions}
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
      {moveError ? <p className="pf-tool-error">{moveError}</p> : null}
      {props.onReordered && set.items.length > 1 ? (
        <p className="pf-muted pf-ecom-order-hint">{text('点卡片上的 ‹ › 可调整顺序，详情长图和打包下载按新顺序排列', 'Use ‹ › on each card to reorder; the long image and zip follow the new order')}</p>
      ) : null}

      <div className="pf-ecom-grid" style={{ marginTop: error ? '0.75rem' : 0 }}>
        {set.items.map((item, pos) => (
          <ResultCard key={item.idx} item={item} pending={busyIdx === item.idx} {...props}
            onMove={canMove ? (dir) => void move(pos, dir) : undefined}
            isFirst={pos === 0} isLast={pos === set.items.length - 1} />
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
  onMove,
  isFirst,
  isLast,
}: Props & { item: EcomSetItem; pending: boolean; onMove?: (dir: -1 | 1) => void; isFirst: boolean; isLast: boolean }) {
  const { t } = useI18n()
  const busy = isItemBusy(item)
  const image = item.final_url ? resolveToolMediaUrl(item.final_url) : ''

  return (
    <article className="pf-ecom-card">
      <div className="pf-ecom-card-head">
        <strong>
          {item.idx + 1}. {item.slot_label}
        </strong>
        <span className="pf-ecom-card-tools">
          {onMove ? (
            <>
              <button type="button" className="pf-ecom-move" disabled={isFirst} aria-label={t('ecomSet.result.moveLeft')}
                title={t('ecomSet.result.moveLeft')} onClick={() => onMove(-1)}>
                <ChevronLeft size={14} aria-hidden />
              </button>
              <button type="button" className="pf-ecom-move" disabled={isLast} aria-label={t('ecomSet.result.moveRight')}
                title={t('ecomSet.result.moveRight')} onClick={() => onMove(1)}>
                <ChevronRight size={14} aria-hidden />
              </button>
            </>
          ) : null}
          <span className="pf-badge">{item.ratio}</span>
        </span>
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
            <img src={image} alt={item.slot_label} loading="lazy" />
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
