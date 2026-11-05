import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listRecentEcom, type EcomRecent } from '../../api/ecomHub'
import { formatDateTime, useI18n } from '../../i18n'
import { ecomHistoryHref, type EcomKind, ECOM_PATHS } from '../../lib/ecomHub'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { pageCountOf } from '../../lib/pagination'
import Button from '../ui/Button'
import Pagination from '../ui/Pagination'

const PAGE_SIZE = 12

/** 最近电商任务：服务端聚合筛选和分页，带加载、空态和错误重试。 */
export default function RecentTasks() {
  const { t } = useI18n()
  /* 筛选与页码放在同一状态中，切类型时原子回到第一页。 */
  const [filter, setFilter] = useState<{ kind: EcomKind | ''; page: number }>({ kind: '', page: 1 })
  const [data, setData] = useState<EcomRecent | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listRecentEcom(filter.page, PAGE_SIZE, filter.kind)
      .then((next) => { if (!cancelled) setData(next) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filter, reload, t])
  return <section className="pf-job-section" id="recent-ecom-tasks">
    <div className="pf-job-head"><h2>{t('ecomHub.recent')}</h2>
      <label className="pf-tool-field"><span>{t('ecomHub.all')}</span>
        <select value={filter.kind} onChange={(event) => setFilter({ kind: event.target.value as EcomKind | '', page: 1 })}>
          <option value="">{t('ecomHub.all')}</option>
          {(Object.keys(ECOM_PATHS) as EcomKind[]).map((kind) => <option key={kind} value={kind}>{t(`ecomHub.kinds.${kind}`)}</option>)}
        </select>
      </label>
    </div>
    {loading ? <p role="status">{t('ecomJobs.loading')}</p> : error ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(error, t)}</p><Button type="button" variant="outline" onClick={() => setReload((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : !data?.items.length ? <p className="pf-muted">{t('ecomHub.empty')}</p> : <div className="pf-job-history-grid">
      {data.items.map((task) => <Link className="pf-job-history-card" key={`${task.kind}:${task.id}`} to={ecomHistoryHref(task.kind, task.id)}>
        <span className="pf-badge">{t(`ecomHub.kinds.${task.kind}`)}</span><strong>{task.title || t(`ecomHub.kinds.${task.kind}`)}</strong>
        <span>{t(`ecomHub.status.${task.status}`)}</span><small>{formatDateTime(task.created_at)}</small>
      </Link>)}
    </div>}
    {!loading && !error && data ? <Pagination page={filter.page} pageCount={pageCountOf(data.meta.total, PAGE_SIZE)} total={data.meta.total} pageSize={PAGE_SIZE} onChange={(page) => setFilter((value) => ({ ...value, page }))} /> : null}
  </section>
}
