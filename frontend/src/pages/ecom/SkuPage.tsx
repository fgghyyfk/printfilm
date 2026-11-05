import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Sparkles } from 'lucide-react'
import {
  createEcomJob, downloadEcomJobImage, downloadEcomJobZip, getEcomJob, getEcomJobOptions,
  retryEcomJobItem, type EcomJob, type EcomJobItem, type EcomJobOptions, type SkuParams,
} from '../../api/ecomJobs'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ImageUpload from '../../components/ecomJobs/ImageUpload'
import JobHistory from '../../components/ecomJobs/JobHistory'
import SkuParameterPanel from '../../components/ecomJobs/SkuParameterPanel'
import SkuProductUpload from '../../components/ecomJobs/SkuProductUpload'
import { validateSkuImages } from '../../lib/ecomSku'
import SourceReference from '../../components/ecomJobs/SourceReference'
import { useRefineSource } from '../../components/ecomSet/useRefineSource'
import ResultsGrid from '../../components/ecomJobs/ResultsGrid'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import { useEcomJobPolling } from '../../hooks/useEcomJobPolling'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import { ecomJobErrorText, ecomJobNeedsPolling, validateEcomImages } from '../../lib/ecomJobs'
import { formatFen } from '../../lib/ecomSet'

const PAGE_PATH = '/ecom/sku'

/** SKU 替换登录守卫，保留当前任务地址。 */
export default function SkuPage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(PAGE_PATH + location.search)}`} replace />
  return <SkuWorkspace />
}

/** SKU 替换页面编排：上传、参数、余额确认、结果轮询与历史。 */
function SkuWorkspace() {
  const { t } = useI18n()
  const [search, setSearch] = useSearchParams()
  const source = useRefineSource()
  const rawId = Number(search.get('id'))
  const jobId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  /* 选项与重载；上传文件和 SKU 参数。 */
  const [options, setOptions] = useState<EcomJobOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [reloadOptions, setReloadOptions] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [sceneFiles, setSceneFiles] = useState<File[]>([])
  const [params, setParams] = useState<SkuParams>({ ratio: null, clarity: '2k', notes: '', sku_names: [] })
  /* 当前任务、加载错误及各操作状态；历史刷新键。 */
  const [job, setJob] = useState<EcomJob | null>(null)
  const [error, setError] = useState('')
  const [reloadJob, setReloadJob] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [historyKey, setHistoryKey] = useState(0)
  const currentIdRef = useRef<number | null>(jobId)
  const kindOptions = options?.kinds.sku
  const maxProducts = kindOptions?.max_images || 20
  const outputCount = files.length
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
    const problem = source.source ? validateEcomImages(files, maxProducts) : validateSkuImages(files, sceneFiles)
    if (problem) { setError(t(problem === 'sceneRequired' ? 'ecomJobs.sku.sceneRequired' : `ecomJobs.errors.${problem}`, { max: maxProducts })); return }
    if (params.sku_names.some((name) => !name.trim())) { setError(t('ecomJobs.sku.nameRequired')); return }
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.sku.confirmTitle'), message: t('ecomJobs.sku.confirm', { count: outputCount, price: formatFen(estimate) }), confirmText: t('ecomJobs.sku.start') })) return
    setCreating(true)
    setError('')
    try {
      const next = await createEcomJob('sku', files, params, [], { scene: source.source ? [] : sceneFiles }, source.source || undefined)
      currentIdRef.current = next.id
      setSearch({ id: String(next.id) })
      setHistoryKey((value) => value + 1)
      setFiles([])
      setParams((value) => ({ ...value, sku_names: [] }))
      setSceneFiles([])
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
        <Link to="/ecom" className="pf-tool-back"><ArrowLeft size={16} aria-hidden />{t('ecomHub.back')}</Link>
        <header className="pf-tool-detail-head"><Sparkles size={26} aria-hidden /><div><h1>{t('ecomJobs.sku.title')}</h1><p className="pf-muted">{t('ecomJobs.sku.lead')}</p></div></header>
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
        {jobId && optionsError ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" variant="outline" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : null}
        {jobId ? (
          <>
            <div className="pf-job-actions"><Button type="button" variant="outline" onClick={() => setSearch({})}>{t('ecomJobs.sku.newJob')}</Button></div>
            {job ? <ResultsGrid job={job} busyIdx={busyIdx} downloading={downloading} onRetry={retry} onDownload={downloadItem} onZip={downloadZip} /> : error ? <Button type="button" variant="outline" onClick={() => setReloadJob((value) => value + 1)}>{t('ecomJobs.retry')}</Button> : <p role="status">{t('ecomJobs.loading')}</p>}
          </>
        ) : optionsError ? (
          <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div>
        ) : !kindOptions ? <p role="status">{t('ecomJobs.loading')}</p> : (
          <section className="pf-job-section">
            <SourceReference value={source} kind="sku" />
            {!source.source ? <ImageUpload files={sceneFiles} maxImages={1} role="scene" labelKey="ecomJobs.sku.scene" disabled={creating} onChange={setSceneFiles} /> : null}
            <SkuProductUpload files={files} names={params.sku_names} maxImages={maxProducts} disabled={creating}
              onChange={(next, names) => { setFiles(next); setParams((value) => ({ ...value, sku_names: names })) }} />
            <SkuParameterPanel value={params} options={kindOptions} disabled={creating} onChange={setParams} />
            <div className="pf-job-actions">
              <Button type="button" variant="lime" disabled={creating || !files.length || (!source.source && sceneFiles.length !== 1) || files.length > maxProducts} onClick={submit}>{creating ? t('ecomJobs.submitting') : t('ecomJobs.sku.start')}</Button>
              {options?.billing_enabled ? <span className="pf-muted">{t('ecomJobs.estimate', { price: formatFen(estimate) })}</span> : null}
            </div>
          </section>
        )}
        <JobHistory kind="sku" currentId={jobId} refreshKey={historyKey} onOpen={(id) => setSearch({ id: String(id) })} />
      </div>
    </AppShell>
  )
}
