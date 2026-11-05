import { Loader2 } from 'lucide-react'
import type { EcomJob, EcomJobItem } from '../../api/ecomJobs'
import { resolveToolMediaUrl } from '../../api/tools'
import { translationProgress } from '../../lib/ecomTranslate'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { useI18n } from '../../i18n'
import { canUseJobVideo } from '../../lib/ecomHub'
import ResultNextMenu from './ResultNextMenu'
import Button from '../ui/Button'

type Props = {
  job: EcomJob
  busyIdx: number | null
  downloading: boolean
  onRetry: (item: EcomJobItem) => void
  onDownload: (item: EcomJobItem) => void
  onZip: () => void
  onVariants?: (item: EcomJobItem) => void
}

/** 通用逐图结果网格：排队、失败、成图及空态，附单张和整批下载。 */
export default function ResultsGrid({ job, busyIdx, downloading, onRetry, onDownload, onZip, onVariants }: Props) {
  const { t } = useI18n()
  const progress = translationProgress(job.items)
  const completed = progress.downloadable
  return (
    <section className="pf-job-section" aria-live="polite">
      <div className="pf-job-head">
        <div><h2>{t(job.kind === 'translate' ? 'ecomJobs.translate.results' : job.kind === 'sku' ? 'ecomJobs.sku.results' : job.kind === 'style' ? 'ecomJobs.style.results' : job.kind === 'buyer_show' ? 'ecomJobs.buyerShow.results' : 'ecomJobs.results')}</h2><p className="pf-muted">{t(`ecomJobs.status.${job.status}`)} · {t('ecomJobs.progress', { done: progress.finished, total: job.items.length })}</p></div>
        <Button type="button" variant="lime" disabled={!completed || downloading} onClick={onZip}>
          {downloading ? t('ecomJobs.downloading') : t('ecomJobs.zip')}
        </Button>
      </div>
      {job.kind === 'translate' && job.params.mock === true ? <p className="pf-muted" role="note">{t('ecomJobs.translate.mock')}</p> : null}
      {progress.skipped ? <p className="pf-muted">{t('ecomJobs.translate.skippedCount', { count: progress.skipped })}</p> : null}
      {!job.items.length ? <p className="pf-muted">{t('ecomJobs.noResults')}</p> : null}
      <div className="pf-job-actions"><Button variant="outline" to={`/ecom/video?job_id=${job.id}`} disabled={!canUseJobVideo(job.items)}>{t('ecomHub.makeVideo')}</Button>{!canUseJobVideo(job.items) ? <span className="pf-muted">{t('ecomHub.minImages')}</span> : null}</div>
      <div className={`pf-job-grid${job.kind === 'translate' ? ' pf-job-translate-grid' : ''}`}>
        {job.items.map((item) => (
          <article className="pf-job-card" key={item.idx}>
            <strong>{job.kind === 'sku' && Array.isArray(job.params.sku_names) ? String(job.params.sku_names[item.idx] || '') : t('ecomJobs.imageNumber', { n: item.idx + 1 })}</strong>
            <div className={job.kind === 'translate' ? 'pf-job-compare' : undefined}>
              {job.kind === 'translate' ? <div>
                <p className="pf-muted">{t('ecomJobs.translate.original')}</p>
                <div className="pf-job-image">{item.inputs[0]?.url ? <a href={resolveToolMediaUrl(item.inputs[0].url)} target="_blank" rel="noreferrer" aria-label={t('ecomJobs.translate.original')}><img src={resolveToolMediaUrl(item.inputs[0].url)} alt={t('ecomJobs.translate.original')} loading="lazy" /></a> : <div className="pf-job-state">{t('ecomJobs.noResults')}</div>}</div>
              </div> : null}
              <div>
                {job.kind === 'translate' ? <p className="pf-muted">{t('ecomJobs.translate.translated')}</p> : null}
                <div className="pf-job-image">
                  {item.status === 'queued' || item.status === 'running' ? (
                    <div className="pf-job-state"><Loader2 className="pf-tool-spin" aria-hidden />{t(item.status === 'running' && job.kind === 'translate' ? 'ecomJobs.translate.generating' : item.status === 'running' && job.kind === 'sku' ? 'ecomJobs.sku.generating' : item.status === 'running' && job.kind === 'buyer_show' ? 'ecomJobs.buyerShow.generating' : job.kind === 'style' && item.status === 'running' ? 'ecomJobs.style.generating' : `ecomJobs.itemStatus.${item.status}`)}</div>
                  ) : item.status === 'failed' ? (
                    <div className="pf-job-state pf-tool-error"><strong>{t('ecomJobs.itemStatus.failed')}</strong><span>{item.error ? ecomJobErrorText(item.error, t) : t('ecomJobs.failed')}</span></div>
                  ) : item.status === 'skipped' ? (
                    <div className="pf-job-state"><strong>{t('ecomJobs.translate.skipped')}</strong><span>{t('ecomJobs.translate.skippedHint')}</span></div>
                  ) : item.result_url ? (
                    <a href={resolveToolMediaUrl(item.result_url)} target="_blank" rel="noreferrer" aria-label={t('ecomJobs.preview')}>
                      <img src={resolveToolMediaUrl(item.result_url)} alt={t('ecomJobs.imageNumber', { n: item.idx + 1 })} loading="lazy" />
                    </a>
                  ) : <div className="pf-job-state">{t('ecomJobs.noResults')}</div>}
                </div>
              </div>
            </div>
            <div className="pf-job-actions">
              {item.status === 'failed' ? <Button type="button" size="sm" variant="outline" disabled={busyIdx !== null} onClick={() => onRetry(item)}>{busyIdx === item.idx ? t('ecomJobs.submitting') : t('ecomJobs.retry')}</Button> : null}
              {item.status === 'succeeded' && item.result_url ? <Button type="button" size="sm" variant="outline" disabled={busyIdx !== null} onClick={() => onDownload(item)}>{t('ecomJobs.download')}</Button> : null}
              {onVariants && item.status === 'succeeded' && item.result_url ? <Button type="button" size="sm" variant="outline" disabled={busyIdx !== null} onClick={() => onVariants(item)}>{t('ecomJobs.buyerShow.variants')}</Button> : null}
              {item.status === 'succeeded' && item.result_url ? <ResultNextMenu jobId={job.id} idx={item.idx} /> : null}
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
