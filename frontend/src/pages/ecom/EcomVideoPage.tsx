import { useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { getEcomJob, type EcomJob } from '../../api/ecomJobs'
import { getJobVideo } from '../../api/ecomHub'
import AppShell from '../../components/layout/AppShell'
import DaihuoVideoPanel from '../../components/daihuo/DaihuoVideoPanel'
import Button from '../../components/ui/Button'
import { useI18n } from '../../i18n'
import { canUseJobVideo, ecomHistoryHref } from '../../lib/ecomHub'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import type { EcomKind } from '../../lib/ecomHub'

/** 批量电商结果转带货的登录守卫。 */
export default function EcomVideoPage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return <EcomVideoWorkspace />
}

/** 先读取所属任务和已有项目，再复用套图的画幅、音色、报价与写稿面板。 */
function EcomVideoWorkspace() {
  const { t } = useI18n()
  const [search] = useSearchParams()
  const raw = search.get('job_id') || ''
  const id = /^[1-9]\d*$/.test(raw) && Number.isSafeInteger(Number(raw)) ? Number(raw) : null
  /* 当前来源与关联项目；加载失败后手动重试。 */
  const [data, setData] = useState<{ job: EcomJob; project_id: number | null } | null>(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let cancelled = false
    setData(null)
    setError('')
    if (!id) { setError(t('ecomHub.sourceError')); return }
    Promise.all([getEcomJob(id), getJobVideo(id)])
      .then(([job, video]) => { if (!cancelled) setData({ job, project_id: video.project_id }) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
    return () => { cancelled = true }
  }, [id, reload, t])
  const current = data?.job.id === id ? data : null
  return <AppShell active="ecom"><div className="pf-tool-detail pf-job-page">
    <Link className="pf-tool-back" to="/ecom">{t('ecomHub.back')}</Link><h1>{t('ecomHub.makeVideo')}</h1>
    {error ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(error, t)}</p><Button type="button" onClick={() => setReload((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : !current ? <p role="status">{t('ecomJobs.loading')}</p> : <>
      <p>{current.job.title || t(`ecomHub.kinds.${current.job.kind}`)}</p>
      <Button to={ecomHistoryHref(current.job.kind as EcomKind, current.job.id)} variant="text">{t('ecomJobs.preview')}</Button>
      {canUseJobVideo(current.job.items) || current.project_id ? <DaihuoVideoPanel key={current.job.id} job={{ id: current.job.id, project_id: current.project_id, title: current.job.title }} /> : <p>{t('ecomHub.minImages')}</p>}
    </>}
  </div></AppShell>
}
