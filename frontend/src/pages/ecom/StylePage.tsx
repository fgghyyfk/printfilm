import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Sparkles } from 'lucide-react'
import {
  createEcomJob, downloadEcomJobImage, downloadEcomJobZip, getEcomJob, getEcomJobOptions,
  retryEcomJobItem, type EcomJob, type EcomJobItem, type EcomJobOptions, type StyleParams,
} from '../../api/ecomJobs'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ImageUpload from '../../components/ecomJobs/ImageUpload'
import JobHistory from '../../components/ecomJobs/JobHistory'
import StyleParameterPanel from '../../components/ecomJobs/StyleParameterPanel'
import SourceReference from '../../components/ecomJobs/SourceReference'
import { useRefineSource } from '../../components/ecomSet/useRefineSource'
import ResultsGrid from '../../components/ecomJobs/ResultsGrid'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import { useEcomJobPolling } from '../../hooks/useEcomJobPolling'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import { ecomJobErrorText, ecomJobNeedsPolling, styleOutputCount, styleProductLimit, validateEcomImages } from '../../lib/ecomJobs'
import { formatFen } from '../../lib/ecomSet'

const PAGE_PATH = '/ecom/style'

/** 风格复刻登录守卫，保留当前任务地址。 */
export default function StylePage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(PAGE_PATH + location.search)}`} replace />
  return <StyleWorkspace />
}

/** 风格复刻页面编排：上传、参数、余额确认、结果轮询与历史。 */
function StyleWorkspace() {
  const { t } = useI18n()
  const [search, setSearch] = useSearchParams()
  const source = useRefineSource()
  const rawId = Number(search.get('id'))
  const jobId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  /* 选项与重载；上传文件和风格参数。 */
  const [options, setOptions] = useState<EcomJobOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [reloadOptions, setReloadOptions] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [styleFiles, setStyleFiles] = useState<File[]>([])
  const [params, setParams] = useState<StyleParams>({ mode: 'single', count: 1, ratio: '1:1', clarity: '2k', notes: '' })
  /* 当前任务、加载错误及各操作状态；历史刷新键。 */
  const [job, setJob] = useState<EcomJob | null>(null)
  const [error, setError] = useState('')
  const [reloadJob, setReloadJob] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [historyKey, setHistoryKey] = useState(0)
  const currentIdRef = useRef<number | null>(jobId)
  const kindOptions = options?.kinds.style
  const maxProducts = kindOptions?.mode_limits?.[params.mode] || styleProductLimit(params.mode)
  const outputCount = styleOutputCount(params.mode, files.length, params.count)
  const estimate = (kindOptions?.prices[params.clarity] || 0) * outputCount

  // 同步 URL 指向，拒绝旧任务请求覆盖新选择。
  useEffect(() => { currentIdRef.current = jobId }, [jobId])
  useEffect(() => {
    let cancelled = false
    setOptionsError('')
    getEcomJobOptions().then((data) => { if (!cancelled) setOptions(data) })
      .catch((err) => { if (!cancelled) setOptionsError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
    return () => { cancelled = true }
  }, [reloadOptions, t])
  useEffect(() => {
    let cancelled = false
    setError('')
    setJob(null)
    if (jobId) {
      getEcomJob(jobId).then((data) => { if (!cancelled) setJob(data) })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
    }
    return () => { cancelled = true }
  }, [jobId, reloadJob, t])

  // 轮询终态后刷新历史；成功返回时清理网络错误。
  const onPolled = useCallback((next: EcomJob) => {
    if (currentIdRef.current !== next.id) return
    setJob(next)
    setError('')
    if (!ecomJobNeedsPolling(next)) setHistoryKey((value) => value + 1)
  }, [])
  const onPollError = useCallback((err: unknown) => setError(err instanceof Error ? err.message : t('ecomJobs.failed')), [t])
  useEcomJobPolling(job, onPolled, onPollError)

  // 整批提交前校验图片，启用计费时明确确认本批预估金额。
  async function submit() {
    if (!kindOptions || creating) return
    const referenceProblem = validateEcomImages(styleFiles, 1)
    if (!source.source && referenceProblem) { setError(t('ecomJobs.style.referenceRequired')); return }
    const problem = validateEcomImages(files, maxProducts)
    if (problem) { setError(t(`ecomJobs.errors.${problem}`, { max: maxProducts })); return }
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.style.confirmTitle'), message: t('ecomJobs.style.confirm', { count: outputCount, price: formatFen(estimate) }), confirmText: t('ecomJobs.style.start') })) return
    setCreating(true)
    setError('')
    try {
      const next = await createEcomJob('style', files, params, source.source ? [] : styleFiles, {}, source.source || undefined)
      currentIdRef.current = next.id
      setSearch({ id: String(next.id) })
      setHistoryKey((value) => value + 1)
      setFiles([])
      setStyleFiles([])
    } catch (err) { onPollError(err) } finally { setCreating(false) }
  }

  // 失败单张重试，收费时确认预估价格；迟到响应不能覆盖其他任务。
  async function retry(item: EcomJobItem) {
    if (!job || busyIdx !== null) return
    if (!options || !kindOptions) { setError(optionsError || t('ecomJobs.loading')); return }
    const id = job.id
    const price = kindOptions?.prices[String(job.params.clarity)] || 0
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.retry'), message: t('ecomJobs.confirmRetry', { price: formatFen(price) }) })) return
    setBusyIdx(item.idx)
    setError('')
    try {
      const next = await retryEcomJobItem(id, item.idx)
      if (currentIdRef.current === id) setJob(next)
      setHistoryKey((value) => value + 1)
    } catch (err) { onPollError(err) } finally { setBusyIdx(null) }
  }

  // 下载单张结果并将下载错误显示到当前任务区。
  async function downloadItem(item: EcomJobItem) {
    if (!job) return
    setBusyIdx(item.idx)
    setError('')
    try { await downloadEcomJobImage(job.id, item) } catch (err) { onPollError(err) } finally { setBusyIdx(null) }
  }

  // 打包下载所有已成功图片。
  async function downloadZip() {
    if (!job) return
    setDownloading(true)
    setError('')
    try { await downloadEcomJobZip(job.id) } catch (err) { onPollError(err) } finally { setDownloading(false) }
  }

  return (
    <AppShell active="ecom">
      <div className="pf-tool-detail pf-job-page">
        <Link to="/ecom/workbench" className="pf-tool-back"><ArrowLeft size={16} aria-hidden />{t('ecomHub.back')}</Link>
        <header className="pf-tool-detail-head"><Sparkles size={26} aria-hidden /><div><h1>{t('ecomJobs.style.title')}</h1><p className="pf-muted">{t('ecomJobs.style.lead')}</p></div></header>
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
        {jobId && optionsError ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" variant="outline" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : null}
        {jobId ? (
          <>
            <div className="pf-job-actions"><Button type="button" variant="outline" onClick={() => setSearch({})}>{t('ecomJobs.style.newJob')}</Button></div>
            {job ? <ResultsGrid job={job} busyIdx={busyIdx} downloading={downloading} onRetry={retry} onDownload={downloadItem} onZip={downloadZip} /> : error ? <Button type="button" variant="outline" onClick={() => setReloadJob((value) => value + 1)}>{t('ecomJobs.retry')}</Button> : <p role="status">{t('ecomJobs.loading')}</p>}
          </>
        ) : optionsError ? (
          <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div>
        ) : !kindOptions ? <p role="status">{t('ecomJobs.loading')}</p> : (
          <section className="pf-job-section">
            <SourceReference value={source} kind="style" />
            {!source.source ? <ImageUpload files={styleFiles} maxImages={1} role="style" disabled={creating} onChange={setStyleFiles} /> : null}
            <ImageUpload files={files} maxImages={maxProducts} disabled={creating} onChange={setFiles} />
            <StyleParameterPanel value={params} options={kindOptions} disabled={creating} onChange={setParams} />
            <div className="pf-job-actions">
              <Button type="button" variant="lime" disabled={creating || !files.length || (!source.source && styleFiles.length !== 1) || files.length > maxProducts} onClick={submit}>{creating ? t('ecomJobs.submitting') : t('ecomJobs.style.start')}</Button>
              {options?.billing_enabled ? <span className="pf-muted">{t('ecomJobs.estimate', { price: formatFen(estimate) })}</span> : null}
            </div>
          </section>
        )}
        <JobHistory kind="style" currentId={jobId} refreshKey={historyKey} onOpen={(id) => setSearch({ id: String(id) })} />
      </div>
    </AppShell>
  )
}
