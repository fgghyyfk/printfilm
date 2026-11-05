import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Sparkles } from 'lucide-react'
import {
  createEcomJob, createEcomJobVariants, downloadEcomJobImage, downloadEcomJobZip, getEcomJob, getEcomJobOptions,
  retryEcomJobItem, type EcomJob, type EcomJobItem, type EcomJobOptions, type BuyerShowParams,
} from '../../api/ecomJobs'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ImageUpload from '../../components/ecomJobs/ImageUpload'
import JobHistory from '../../components/ecomJobs/JobHistory'
import BuyerShowParameterPanel from '../../components/ecomJobs/BuyerShowParameterPanel'
import ResultsGrid from '../../components/ecomJobs/ResultsGrid'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import { useEcomJobPolling } from '../../hooks/useEcomJobPolling'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import { ecomJobErrorText, ecomJobNeedsPolling, validateBuyerShowImages } from '../../lib/ecomJobs'
import { formatFen } from '../../lib/ecomSet'

const PAGE_PATH = '/ecom/buyer-show'

/** 买家秀 / 种草图登录守卫，保留当前任务地址。 */
export default function BuyerShowPage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(PAGE_PATH + location.search)}`} replace />
  return <BuyerShowWorkspace />
}

/** 买家秀 / 种草图页面编排：上传、参数、余额确认、结果轮询、裂变与历史。 */
function BuyerShowWorkspace() {
  const { t } = useI18n()
  const [search, setSearch] = useSearchParams()
  const rawId = Number(search.get('id'))
  const jobId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  /* 选项与重载；产品、模特、场景上传文件及生成参数。 */
  const [options, setOptions] = useState<EcomJobOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [reloadOptions, setReloadOptions] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [modelFiles, setModelFiles] = useState<File[]>([])
  const [sceneFiles, setSceneFiles] = useState<File[]>([])
  const [params, setParams] = useState<BuyerShowParams>({ mode: 'buyer', count: 1, ratio: '3:4', clarity: '2k', notes: '' })
  /* 当前任务、加载错误及各操作状态；历史刷新键。 */
  const [job, setJob] = useState<EcomJob | null>(null)
  const [error, setError] = useState('')
  const [reloadJob, setReloadJob] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [historyKey, setHistoryKey] = useState(0)
  const currentIdRef = useRef<number | null>(jobId)
  const kindOptions = options?.kinds.buyer_show
  const maxProducts = kindOptions?.max_images || 9
  const outputCount = params.count
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
      getEcomJob(jobId).then((data) => {
        if (data.kind !== 'buyer_show') throw new Error(t('ecomJobs.serverErrors.notFound'))
        if (!cancelled) setJob(data)
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
  const onPollError = useCallback((err: unknown) => setError(err instanceof Error ? err.message : t('ecomJobs.failed')), [t])
  useEcomJobPolling(job, onPolled, onPollError)

  // 整批提交前校验图片，启用计费时明确确认本批预估金额。
  async function submit() {
    if (!kindOptions || creating) return
    const problem = validateBuyerShowImages(files, modelFiles, sceneFiles)
    if (problem) { setError(problem === 'optional' ? t('ecomJobs.buyerShow.optionalError') : t(`ecomJobs.errors.${problem}`, { max: maxProducts })); return }
    if (options?.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.buyerShow.confirmTitle'), message: t('ecomJobs.buyerShow.confirm', { count: outputCount, price: formatFen(estimate) }), confirmText: t('ecomJobs.buyerShow.start') })) return
    setCreating(true)
    setError('')
    try {
      const next = await createEcomJob('buyer_show', files, params, [], { model: modelFiles, scene: sceneFiles })
      currentIdRef.current = next.id
      setSearch({ id: String(next.id) })
      setHistoryKey((value) => value + 1)
      setFiles([])
      setModelFiles([])
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

  // 裂变复用当前任务参数，先选择数量并确认费用，再创建独立任务。
  async function variants(item: EcomJobItem) {
    if (!job || busyIdx !== null || !options || !kindOptions) return
    const id = job.id
    setBusyIdx(item.idx)
    setError('')
    try {
      const raw = await dialog.prompt({ title: t('ecomJobs.buyerShow.variantsTitle'), message: t('ecomJobs.buyerShow.variantsCount'), defaultValue: '3' })
      if (raw === null) return
      const count = Number(raw)
      if (!Number.isInteger(count) || count < 1 || count > 8) { setError(t('ecomJobs.buyerShow.variantsInvalid')); return }
      const price = (kindOptions.prices[String(job.params.clarity)] || 0) * count
      if (options.billing_enabled && !await dialog.confirm({ title: t('ecomJobs.buyerShow.variantsTitle'), message: t('ecomJobs.buyerShow.variantsConfirm', { count, price: formatFen(price) }) })) return
      // 等待对话框期间切换历史任务则取消这次提交。
      if (currentIdRef.current !== id) return
      const next = await createEcomJobVariants(id, item.idx, count)
      if (currentIdRef.current === id) {
        currentIdRef.current = next.id
        setSearch({ id: String(next.id) })
      }
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
    <AppShell active="tools">
      <div className="pf-tool-detail pf-job-page">
        <Link to="/tools" className="pf-tool-back"><ArrowLeft size={16} aria-hidden />{t('ecomJobs.back')}</Link>
        <header className="pf-tool-detail-head"><Sparkles size={26} aria-hidden /><div><h1>{t('ecomJobs.buyerShow.title')}</h1><p className="pf-muted">{t('ecomJobs.buyerShow.lead')}</p></div></header>
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
        {jobId && optionsError ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" variant="outline" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div> : null}
        {jobId ? (
          <>
            <div className="pf-job-actions"><Button type="button" variant="outline" onClick={() => setSearch({})}>{t('ecomJobs.buyerShow.newJob')}</Button></div>
            {job ? <ResultsGrid job={job} busyIdx={busyIdx} downloading={downloading} onRetry={retry} onDownload={downloadItem} onZip={downloadZip} onVariants={variants} /> : error ? <Button type="button" variant="outline" onClick={() => setReloadJob((value) => value + 1)}>{t('ecomJobs.retry')}</Button> : <p role="status">{t('ecomJobs.loading')}</p>}
          </>
        ) : optionsError ? (
          <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" onClick={() => setReloadOptions((value) => value + 1)}>{t('ecomJobs.retry')}</Button></div>
        ) : !kindOptions ? <p role="status">{t('ecomJobs.loading')}</p> : (
          <section className="pf-job-section">
            <ImageUpload files={files} maxImages={maxProducts} disabled={creating} onChange={setFiles} />
            <ImageUpload files={modelFiles} maxImages={1} role="model" disabled={creating} onChange={setModelFiles} />
            <ImageUpload files={sceneFiles} maxImages={1} role="scene" disabled={creating} onChange={setSceneFiles} />
            <BuyerShowParameterPanel value={params} options={kindOptions} disabled={creating} onChange={setParams} />
            <div className="pf-job-actions">
              <Button type="button" variant="lime" disabled={creating || !files.length || files.length > maxProducts} onClick={submit}>{creating ? t('ecomJobs.submitting') : t('ecomJobs.buyerShow.start')}</Button>
              {options?.billing_enabled ? <span className="pf-muted">{t('ecomJobs.estimate', { price: formatFen(estimate) })}</span> : null}
            </div>
          </section>
        )}
        <JobHistory kind="buyer_show" currentId={jobId} refreshKey={historyKey} onOpen={(id) => setSearch({ id: String(id) })} />
      </div>
    </AppShell>
  )
}
