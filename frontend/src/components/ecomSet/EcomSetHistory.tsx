import { useEffect, useState } from 'react'
import { ImageIcon, Loader2 } from 'lucide-react'
import { listEcomSets, type EcomSet } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { formatDateTime, useI18n } from '../../i18n'
import { ecomSetCover } from '../../lib/ecomSet'
import { pageCountOf } from '../../lib/pagination'
import Button from '../ui/Button'
import Pagination from '../ui/Pagination'

const PAGE_SIZE = 12

type Props = {
  currentId: number | null
  /** 变化时重新拉取当前页 */
  refreshKey: number
  onOpen: (id: number) => void
}

/** 「我的套图」历史：服务端分页，点击打开已有套图 */
export default function EcomSetHistory({ currentId, refreshKey, onOpen }: Props) {
  const { t } = useI18n()
  /*
   * page 页码；items 当前页；total 总数
   * loading 加载中；error 错误；retryKey 手动重试
   */
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<EcomSet[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listEcomSets(page, PAGE_SIZE)
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : t('ecomSet.loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [page, refreshKey, retryKey, t])

  return (
    <section className="pf-ecom-history" aria-label={t('ecomSet.history.title')}>
      <div className="pf-ecom-section-head">
        <h2>{t('ecomSet.history.title')}</h2>
      </div>
      {loading && !items.length ? (
        <div className="pf-ecom-center">
          <Loader2 size={22} className="pf-tool-spin" aria-hidden />
          {t('ecomSet.loading')}
        </div>
      ) : error ? (
        <div className="pf-ecom-center">
          <span className="pf-tool-error">{error}</span>
          <Button variant="outline" size="sm" onClick={() => setRetryKey((k) => k + 1)}>
            {t('ecomSet.retry')}
          </Button>
        </div>
      ) : !items.length ? (
        <div className="pf-empty-state is-compact">
          <p className="pf-muted">{t('ecomSet.history.empty')}</p>
        </div>
      ) : (
        <>
          <div className="pf-ecom-history-grid">
            {items.map((set) => {
              const cover = ecomSetCover(set)
              const isCurrent = set.id === currentId
              return (
                <button
                  key={set.id}
                  type="button"
                  className={`pf-ecom-history-card${isCurrent ? ' is-current' : ''}`}
                  onClick={() => onOpen(set.id)}
                >
                  {cover ? (
                    <img src={resolveToolMediaUrl(cover)} alt="" loading="lazy" />
                  ) : (
                    <span className="pf-ecom-history-ph pf-ecom-thumb-state">
                      <ImageIcon size={20} aria-hidden />
                    </span>
                  )}
                  <strong>{set.name}</strong>
                  <span className="pf-ecom-history-meta">
                    <span className={`pf-badge ${statusTone(set.status)}`}>{t(`ecomSet.status.${set.status}`)}</span>
                    <span>{t('ecomSet.history.count', { count: set.count })}</span>
                  </span>
                  <span className="pf-ecom-history-meta">{formatDateTime(set.created_at)}</span>
                </button>
              )
            })}
          </div>
          <Pagination page={page} pageCount={pageCountOf(total, PAGE_SIZE)} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
        </>
      )}
    </section>
  )
}

// 状态徽标配色
function statusTone(status: EcomSet['status']): string {
  if (status === 'done') return 'ok'
  if (status === 'generating') return 'run'
  if (status === 'failed' || status === 'partial') return 'bad'
  return ''
}
