import { Loader2 } from 'lucide-react'
import type { EcomJob, EcomJobItem } from '../../api/ecomJobs'
import { resolveToolMediaUrl } from '../../api/tools'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { useI18n } from '../../i18n'
import Button from '../ui/Button'

type Props = {
  job: EcomJob
  busyIdx: number | null
  downloading: boolean
  onRetry: (item: EcomJobItem) => void
  onDownload: (item: EcomJobItem) => void
  onZip: () => void
}

/** 通用逐图结果网格：排队、失败、成图及空态，附单张和整批下载。 */
export default function ResultsGrid({ job, busyIdx, downloading, onRetry, onDownload, onZip }: Props) {
  const { t } = useI18n()
  const completed = job.items.filter((item) => item.status === 'succeeded' && item.result_url).length
  return (
    <section className="pf-job-section" aria-live="polite">
      <div className="pf-job-head">
        <div><h2>{t('ecomJobs.results')}</h2><p className="pf-muted">{t(`ecomJobs.status.${job.status}`)} · {t('ecomJobs.progress', { done: completed, total: job.items.length })}</p></div>
        <Button type="button" variant="lime" disabled={!completed || downloading} onClick={onZip}>
          {downloading ? t('ecomJobs.downloading') : t('ecomJobs.zip')}
        </Button>
      </div>
      {!job.items.length ? <p className="pf-muted">{t('ecomJobs.noResults')}</p> : null}
      <div className="pf-job-grid">
        {job.items.map((item) => (
          <article className="pf-job-card" key={item.idx}>
            <strong>{t('ecomJobs.imageNumber', { n: item.idx + 1 })}</strong>
            <div className="pf-job-image">
              {item.status === 'queued' || item.status === 'running' ? (
                <div className="pf-job-state"><Loader2 className="pf-tool-spin" aria-hidden />{t(`ecomJobs.itemStatus.${item.status}`)}</div>
              ) : item.status === 'failed' ? (
                <div className="pf-job-state pf-tool-error"><strong>{t('ecomJobs.itemStatus.failed')}</strong><span>{item.error ? ecomJobErrorText(item.error, t) : t('ecomJobs.failed')}</span></div>
              ) : item.result_url ? (
                <a href={resolveToolMediaUrl(item.result_url)} target="_blank" rel="noreferrer" aria-label={t('ecomJobs.preview')}>
                  <img src={resolveToolMediaUrl(item.result_url)} alt={t('ecomJobs.imageNumber', { n: item.idx + 1 })} loading="lazy" />
                </a>
              ) : <div className="pf-job-state">{t('ecomJobs.noResults')}</div>}
            </div>
            <div className="pf-job-actions">
              {item.status === 'failed' ? <Button type="button" size="sm" variant="outline" disabled={busyIdx !== null} onClick={() => onRetry(item)}>{busyIdx === item.idx ? t('ecomJobs.submitting') : t('ecomJobs.retry')}</Button> : null}
              {item.status === 'succeeded' && item.result_url ? <Button type="button" size="sm" variant="outline" disabled={busyIdx !== null} onClick={() => onDownload(item)}>{t('ecomJobs.download')}</Button> : null}
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
