import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Sparkles } from 'lucide-react'
import {
  createEcomJob, downloadEcomJobImage, downloadEcomJobZip, getEcomJob, getEcomJobOptions,
  retryEcomJobItem, type EcomJob, type EcomJobItem, type EcomJobOptions, type TranslateParams,
} from '../../api/ecomJobs'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ImageUpload from '../../components/ecomJobs/ImageUpload'
import JobHistory from '../../components/ecomJobs/JobHistory'
import ResultsGrid from '../../components/ecomJobs/ResultsGrid'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import { useEcomJobPolling } from '../../hooks/useEcomJobPolling'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import { ecomJobErrorText, ecomJobNeedsPolling, validateEcomImages } from '../../lib/ecomJobs'
import { formatFen } from '../../lib/ecomSet'

const PAGE_PATH = '/ecom/translate'

/** 图片翻译登录守卫，保留当前任务地址。 */
export default function TranslatePage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(PAGE_PATH + location.search)}`} replace />
  return <TranslateWorkspace />
}

/** 图片翻译页面编排：上传、参数、余额确认、结果轮询与历史。 */
function TranslateWorkspace() {
  const { t } = useI18n()
  const [search, setSearch] = useSearchParams()
  const rawId = Number(search.get('id'))
  const jobId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  /* 选项与重载；上传文件和翻译参数。 */
  const [options, setOptions] = useState<EcomJobOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [reloadOptions, setReloadOptions] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [params, setParams] = useState<TranslateParams>({ language: 'en', glossary: '' })
  /* 当前任务、加载错误及各操作状态；历史刷新键。 */
  const [job, setJob] = useState<EcomJob | null>(null)
  const [error, setError] = useState('')
  const [reloadJob, setReloadJob] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [historyKey, setHistoryKey] = useState(0)
  const currentIdRef = useRef<number | null>(jobId)
  const kindOptions = options?.kinds.translate
  const maxProducts = kindOptions?.max_images || 20
  const outputCount = files.length
  const estimate = (kindOptions?.prices['2k'] || 0) * outputCount

  // 同步 URL 指向，拒绝旧任务请求覆盖新选择。
  useEffect(() => { currentIdRef.current = jobId }, [jobId])
  // 加载功能上限与计费选项，失败时保留重试入口。
  useEffect(() => {
    let cancelled = false
    setOptionsError('')
    getEcomJobOptions().then((data) => { if (!cancelled) setOptions(data) })
      .catch((err) => { if (!cancelled) setOptionsError(err instanceof Error ? err.message : t('ecomJobs.failed')) })
    return () => { cancelled = true }
  }, [reloadOptions, t])
  // URL 切换后重新加载任务，忽略旧页面请求。
  useEffect(() => {
    let cancelled = false
    setError('')
    setJob(null)
    if (jobId) {
      getEcomJob(jobId).then((data) => {
        if (!cancelled) {
          if (data.kind !== 'translate') { setError(t('ecomJobs.serverErrors.notFound')); return }
          setJob(data)
        }
      })
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
  // 轮询错误在当前任务区显示。
  const onPollError = useCallback((err: unknown) => setError(err instanceof Error ? err.message : t('ecomJobs.failed')), [t])
  useEcomJobPolling(job, onPolled, onPollError)

  // 整批提交前校验图片，启用计费时明确确认本批预估金额。
  async function submit() {
    if (!kindOptions || creating) return
    const problem = validateEcomImages(files, maxProducts)
    if (problem) { setError(t(problem === 'required' ? 'ecomJobs.translate.required' : `ecomJobs.errors.${problem}`, { max: maxProducts })); return }
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.translate.confirmTitle'), message: t('ecomJobs.translate.confirm', { count: outputCount, price: formatFen(estimate) }), confirmText: t('ecomJobs.translate.start') })) return
    if (currentIdRef.current !== null) return
    setCreating(true)
    setError('')
    try {
      const next = await createEcomJob('translate', files, params)
      if (currentIdRef.current !== null) return
      currentIdRef.current = next.id
      setSearch({ id: String(next.id) })
      setHistoryKey((value) => value + 1)
      setFiles([])
    } catch (err) { if (currentIdRef.current === null) onPollError(err) } finally { setCreating(false) }
  }

  // 失败单张重试，收费时确认预估价格；迟到响应不能覆盖其他任务。
  async function retry(item: EcomJobItem) {
    if (!job || busyIdx !== null) return
    if (!options || !kindOptions) { setError(optionsError || t('ecomJobs.loading')); return }
    const id = job.id
    const price = kindOptions.prices['2k'] || 0
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.retry'), message: t('ecomJobs.confirmRetry', { price: formatFen(price) }) })) return
    if (currentIdRef.current !== id) return
    setBusyIdx(item.idx)
    setError('')
    try {
      const next = await retryEcomJobItem(id, item.idx)
      if (currentIdRef.current === id) setJob(next)
      setHistoryKey((value) => value + 1)
    } catch (err) { if (currentIdRef.current === id) onPollError(err) } finally { setBusyIdx(null) }
  }

  // 下载单张结果并将下载错误显示到当前任务区。
  async function downloadItem(item: EcomJobItem) {
    if (!job) return
    const id = job.id
    setBusyIdx(item.idx)
    setError('')
    try { await downloadEcomJobImage(id, item) } catch (err) { if (currentIdRef.current === id) onPollError(err) } finally { setBusyIdx(null) }
  }

  // 打包下载所有已成功图片。
  async function downloadZip() {
    if (!job) return
    const id = job.id
    setDownloading(true)
    setError('')
    try { await downloadEcomJobZip(id) } catch (err) { if (currentIdRef.current === id) onPollError(err) } finally { setDownloading(false) }
  }

  return (
    <AppShell active="ecom">
      <div className="pf-tool-detail pf-job-page">
        <Link to="/ecom/workbench" className="pf-tool-back"><ArrowLeft size={16} aria-hidden />{t('ecomHub.back')}</Link>
        <header className="pf-tool-detail-head"><Sparkles size={26} aria-hidden /><div><h1>{t('ecomJobs.translate.title')}</h1><p className="pf-muted">{t('ecomJobs.translate.lead')}</p></div></header>
        <p><Link to="/daihuo">{t('ecomJobs.translate.setLink')}</Link></p>
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
        {jobId && optionsError ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" variant="outline" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : null}
        {jobId ? (
          <>
            <div className="pf-job-actions"><Button type="button" variant="outline" onClick={() => setSearch({})}>{t('ecomJobs.translate.newJob')}</Button></div>
            {job ? <ResultsGrid job={job} busyIdx={busyIdx} downloading={downloading} onRetry={retry} onDownload={downloadItem} onZip={downloadZip} /> : error ? <Button type="button" variant="outline" onClick={() => setReloadJob((value) => value + 1)}>{t('ecomJobs.retry')}</Button> : <p role="status">{t('ecomJobs.loading')}</p>}
          </>
        ) : optionsError ? (
          <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div>
        ) : !kindOptions ? <p role="status">{t('ecomJobs.loading')}</p> : (
          <section className="pf-job-section">
            <ImageUpload files={files} maxImages={maxProducts} role="source" labelKey="ecomJobs.translate.images" disabled={creating} onChange={setFiles} />
            <div className="pf-job-params">
              <label>{t('ecomJobs.translate.language')}<select value={params.language} disabled={creating} onChange={(event) => setParams((value) => ({ ...value, language: event.target.value as TranslateParams['language'] }))}>
                {(['zh', 'en', 'ja'] as const).map((language) => <option key={language} value={language}>{t(`ecomJobs.translate.languages.${language}`)}</option>)}
              </select></label>
              <label className="pf-job-notes">{t('ecomJobs.translate.glossary')}<textarea maxLength={2000} value={params.glossary} disabled={creating} placeholder={t('ecomJobs.translate.glossaryHint')} onChange={(event) => setParams((value) => ({ ...value, glossary: event.target.value }))} /></label>
            </div>
            <p className="pf-muted">{t('ecomJobs.translate.hint')}</p>
            <div className="pf-job-actions">
              <Button type="button" variant="lime" disabled={creating || !files.length || files.length > maxProducts} onClick={submit}>{creating ? t('ecomJobs.submitting') : t('ecomJobs.translate.start')}</Button>
              {options?.billing_enabled ? <span className="pf-muted">{t('ecomJobs.translate.estimate', { price: formatFen(estimate) })}</span> : null}
            </div>
          </section>
        )}
        <JobHistory kind="translate" currentId={jobId} refreshKey={historyKey} onOpen={(id) => setSearch({ id: String(id) })} />
      </div>
    </AppShell>
  )
}
