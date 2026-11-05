import { useEffect, useState } from 'react'
import { listEcomJobs, type EcomJob } from '../../api/ecomJobs'
import { resolveToolMediaUrl } from '../../api/tools'
import { formatDateTime, useI18n } from '../../i18n'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { pageCountOf } from '../../lib/pagination'
import Button from '../ui/Button'
import Pagination from '../ui/Pagination'

const PAGE_SIZE = 8

type Props = { kind: string; currentId: number | null; refreshKey: number; onOpen: (id: number) => void }

/** 按功能加载任务历史，分页和总数以服务端为准。 */
export default function JobHistory({ kind, currentId, refreshKey, onOpen }: Props) {
  const { t } = useI18n()
  // 按任务类型选择标题，分页和数据请求保持共用。
  const copy = kind === 'sku' ? 'ecomJobs.sku' : kind === 'style' ? 'ecomJobs.style' : kind === 'buyer_show' ? 'ecomJobs.buyerShow' : 'ecomJobs'
  /* 当前页、当前页数据及总数；独立维护加载、错误与重试状态。 */
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<EcomJob[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listEcomJobs(kind, page, PAGE_SIZE)
      .then((data) => { if (!cancelled) { setItems(data.items); setTotal(data.meta.total) } })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [kind, page, refreshKey, reload, t])
  return (
    <section className="pf-job-section">
      <h2>{t(`${copy}.history`)}</h2>
      {loading ? <p role="status" className="pf-muted">{t('ecomJobs.loading')}</p> : error ? (
        <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(error, t)}</p><Button type="button" variant="outline" onClick={() => setReload((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div>
      ) : !items.length ? <p className="pf-muted">{t(`${copy}.noHistory`)}</p> : (
        <div className="pf-job-history-grid">
          {items.map((job) => {
            const cover = job.items.find((item) => item.result_url)?.result_url || job.items[0]?.inputs[0]?.url
            return (
              <button type="button" className={`pf-job-history-card${job.id === currentId ? ' is-current' : ''}`} key={job.id} onClick={() => onOpen(job.id)}>
                {cover ? <img src={resolveToolMediaUrl(cover)} alt="" loading="lazy" /> : null}
                <strong>{job.title || t(`${copy}.title`)}</strong>
                <span>{t(`ecomJobs.status.${job.status}`)}</span>
                <small>{formatDateTime(job.created_at)}</small>
              </button>
            )
          })}
        </div>
      )}
      {!loading && !error ? <Pagination page={page} pageCount={pageCountOf(total, PAGE_SIZE)} total={total} pageSize={PAGE_SIZE} onChange={setPage} /> : null}
    </section>
  )
}
