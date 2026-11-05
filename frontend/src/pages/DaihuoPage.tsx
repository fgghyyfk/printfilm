import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ShoppingBag, Loader2 } from 'lucide-react'
import {
  downloadEcomSetZip,
  generateEcomSet,
  getEcomSet,
  getEcomSetOptions,
  planEcomSet,
  regenerateEcomSetItem,
  saveEcomSetPlan,
  updateEcomSetItemCopy,
  type EcomSet,
  type EcomSetCopyPatch,
  type EcomSetItem,
  type EcomSetOptions,
  type EcomSetPlanPayload,
} from '../api/ecomSet'
import EcomSetForm, { type EcomSetFormInitial } from '../components/ecomSet/EcomSetForm'
import EcomSetHistory from '../components/ecomSet/EcomSetHistory'
import { EcomSetCopyModal, EcomSetPreviewModal, EcomSetRegenModal } from '../components/ecomSet/EcomSetModals'
import EcomSetPlan, { type EcomPlanBusy } from '../components/ecomSet/EcomSetPlan'
import EcomSetResults from '../components/ecomSet/EcomSetResults'
import DaihuoVideoPanel from '../components/daihuo/DaihuoVideoPanel'
import '../components/ecomSet/ecomSet.css'
import AppShell from '../components/layout/AppShell'
import Button from '../components/ui/Button'
import Stepper from '../components/ui/Stepper'
import { useEcomSetPolling } from '../hooks/useEcomSetPolling'
import { useI18n } from '../i18n'
import { dialog } from '../lib/dialog'
import {
  canMakeDaihuoVideo,
  daihuoStepIndex,
  defaultZipName,
  ecomSetTotalFen,
  formatFen,
  needsPolling,
  parseSetId,
  resolveEcomStep,
  toCopyPatch,
  toPlanPatch,
} from '../lib/ecomSet'

const PAGE_PATH = '/daihuo'

/** 商品带货页：未登录跳转登录，登录后进入「套图 → 带货视频」五步工作台 */
export default function DaihuoPage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) {
    return <Navigate to={`/auth?next=${encodeURIComponent(PAGE_PATH + location.search)}`} replace />
  }
  return <EcomSetWorkspace />
}

/** 五步工作台编排：表单 → 方案 → 生成中 → 完成 → 带货视频，URL ?id= 保持当前套图 */
function EcomSetWorkspace() {
  const { t, m } = useI18n()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const setId = parseSetId(params.get('id'))

  /* 表单选项：options 数据；optionsError 加载失败；optionsKey 重试 */
  const [options, setOptions] = useState<EcomSetOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [optionsKey, setOptionsKey] = useState(0)

  /* 当前套图：set 数据；loadError 打开失败；loadKey 重试；planVersion 保存后重置方案编辑稿 */
  const [set, setSet] = useState<EcomSet | null>(null)
  const [loadError, setLoadError] = useState('')
  const [loadKey, setLoadKey] = useState(0)
  const [planVersion, setPlanVersion] = useState(0)
  const currentIdRef = useRef<number | null>(null)

  /* 操作状态：formBusy 规划中；planBusy 方案保存/生成；busyIdx 单张重试中；downloading 打包下载 */
  const [formBusy, setFormBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const [formInitial, setFormInitial] = useState<EcomSetFormInitial | undefined>(undefined)
  const [planBusy, setPlanBusy] = useState<EcomPlanBusy>(null)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [actionError, setActionError] = useState('')

  /* 弹层：copyItem 改文案；regenItem 重新生成；previewItem 放大预览；modalBusy/modalError 弹层提交态 */
  const [copyItem, setCopyItem] = useState<EcomSetItem | null>(null)
  const [regenItem, setRegenItem] = useState<EcomSetItem | null>(null)
  const [previewItem, setPreviewItem] = useState<EcomSetItem | null>(null)
  const [modalBusy, setModalBusy] = useState(false)
  const [modalError, setModalError] = useState('')

  /* historyKey 变化时刷新「我的套图」 */
  const [historyKey, setHistoryKey] = useState(0)

  const billingEnabled = Boolean(options?.billing_enabled)
  const step = resolveEcomStep(set)

  // 写入当前套图并同步 URL ?id=
  const commitSet = useCallback(
    (next: EcomSet) => {
      currentIdRef.current = next.id
      setSet(next)
      if (parseSetId(params.get('id')) !== next.id) setParams({ id: String(next.id) })
    },
    [params, setParams],
  )

  // 统一错误出口：未登录跳登录页，其余交给对应区域展示
  const failWith = useCallback(
    (err: unknown, show: (message: string) => void) => {
      const message = err instanceof Error ? err.message : t('ecomSet.loadFailed')
      if (message === '未登录' || message === t('ecomSet.errors.notLoggedIn')) {
        nav(`/auth?next=${encodeURIComponent(PAGE_PATH + window.location.search)}`, { replace: true })
        return
      }
      show(message)
    },
    [nav, t],
  )

  // 加载表单选项
  useEffect(() => {
    let cancelled = false
    setOptionsError('')
    getEcomSetOptions()
      .then((data) => {
        if (!cancelled) setOptions(data)
      })
      .catch((err) => {
        if (!cancelled) failWith(err, setOptionsError)
      })
    return () => {
      cancelled = true
    }
  }, [optionsKey, failWith])

  // URL 中 id 变化时加载对应套图（刷新不丢）
  useEffect(() => {
    if (setId == null) {
      currentIdRef.current = null
      setSet(null)
      return
    }
    if (currentIdRef.current === setId) return
    let cancelled = false
    setSet(null)
    setLoadError('')
    setActionError('')
    getEcomSet(setId)
      .then((data) => {
        if (cancelled) return
        currentIdRef.current = data.id
        setSet(data)
      })
      .catch((err) => {
        if (!cancelled) failWith(err, setLoadError)
      })
    return () => {
      cancelled = true
    }
  }, [setId, loadKey, failWith])

  // 轮询回调：进入终态时刷新历史列表
  const onPolled = useCallback((next: EcomSet) => {
    if (currentIdRef.current !== next.id) return
    setSet(next)
    if (!needsPolling(next)) setHistoryKey((k) => k + 1)
  }, [])
  useEcomSetPolling(set, onPolled)

  // 回到第①步（可带预填值）
  function backToForm(initial?: EcomSetFormInitial) {
    currentIdRef.current = null
    setSet(null)
    setFormInitial(initial)
    setFormError('')
    setActionError('')
    setParams({})
  }

  // ① 提交规划（收费时先确认）
  async function onPlan(payload: EcomSetPlanPayload) {
    if (billingEnabled && options) {
      const ok = await dialog.confirm({
        title: t('ecomSet.confirm.planTitle'),
        message: t('ecomSet.confirm.plan', { price: formatFen(options.plan_fen) }),
        confirmText: t('ecomSet.confirm.planOk'),
      })
      if (!ok) return
    }
    setFormBusy(true)
    setFormError('')
    try {
      const next = await planEcomSet(payload)
      setPlanVersion((v) => v + 1)
      commitSet(next)
      setHistoryKey((k) => k + 1)
    } catch (err) {
      failWith(err, setFormError)
    } finally {
      setFormBusy(false)
    }
  }

  // ② 保存方案修改
  async function onSavePlan(items: EcomSetItem[]) {
    if (!set) return
    setPlanBusy('save')
    setActionError('')
    try {
      commitSet(await saveEcomSetPlan(set.id, toPlanPatch(items)))
      setPlanVersion((v) => v + 1)
    } catch (err) {
      failWith(err, setActionError)
    } finally {
      setPlanBusy(null)
    }
  }

  // ② 确认生成：有未保存修改时先保存，再批量生成
  async function onGenerate(items: EcomSetItem[], dirty: boolean) {
    if (!set) return
    const count = set.items.length
    const ok = await dialog.confirm({
      title: t('ecomSet.confirm.generateTitle'),
      message: billingEnabled
        ? t('ecomSet.confirm.generateWithPrice', { count, price: formatFen(ecomSetTotalFen(set)) })
        : t('ecomSet.confirm.generate', { count }),
      confirmText: t('ecomSet.confirm.ok'),
    })
    if (!ok) return
    setPlanBusy('generate')
    setActionError('')
    try {
      if (dirty) {
        commitSet(await saveEcomSetPlan(set.id, toPlanPatch(items)))
        setPlanVersion((v) => v + 1)
      }
      commitSet(await generateEcomSet(set.id))
      setHistoryKey((k) => k + 1)
    } catch (err) {
      failWith(err, setActionError)
    } finally {
      setPlanBusy(null)
    }
  }

  // ② 重新规划：确认后回到表单，沿用品名等参数
  async function onReplan() {
    if (!set) return
    const ok = await dialog.confirm({
      title: t('ecomSet.confirm.replanTitle'),
      message: billingEnabled ? t('ecomSet.confirm.replan') : t('ecomSet.confirm.replanFree'),
      confirmText: t('ecomSet.confirm.replanOk'),
      tone: 'danger',
    })
    if (!ok) return
    backToForm({ name: set.name, platform: set.platform, style: set.style, count: set.count, slots: set.items.map((item) => item.slot), language: set.language, clarity: set.clarity })
  }

  // ③ 失败单张直接重试（收费时确认）
  async function onRetry(item: EcomSetItem) {
    if (!set) return
    if (billingEnabled) {
      const ok = await dialog.confirm({
        title: t('ecomSet.confirm.retryTitle'),
        message: t('ecomSet.confirm.retry', { label: item.slot_label, price: formatFen(set.image_fen) }),
      })
      if (!ok) return
    }
    setBusyIdx(item.idx)
    setActionError('')
    try {
      commitSet(await regenerateEcomSetItem(set.id, item.idx))
    } catch (err) {
      failWith(err, setActionError)
    } finally {
      setBusyIdx(null)
    }
  }

  // 打开弹层前清理上次错误
  function openModal(kind: 'copy' | 'regen', item: EcomSetItem) {
    setModalError('')
    if (kind === 'copy') setCopyItem(item)
    else setRegenItem(item)
  }

  // ④ 重新生成（弹层内可改画面）
  async function onRegenConfirm(scene: string | undefined) {
    if (!set || !regenItem) return
    setModalBusy(true)
    setModalError('')
    try {
      commitSet(await regenerateEcomSetItem(set.id, regenItem.idx, scene))
      setRegenItem(null)
    } catch (err) {
      failWith(err, setModalError)
    } finally {
      setModalBusy(false)
    }
  }

  // ④ 改文案并同步重新排版
  async function onCopySave(copy: EcomSetCopyPatch) {
    if (!set || !copyItem) return
    setModalBusy(true)
    setModalError('')
    try {
      commitSet(await updateEcomSetItemCopy(set.id, copyItem.idx, toCopyPatch(copy)))
      setCopyItem(null)
    } catch (err) {
      failWith(err, setModalError)
    } finally {
      setModalBusy(false)
    }
  }

  // ④ 打包下载 zip
  async function onDownload() {
    if (!set) return
    setDownloading(true)
    setActionError('')
    try {
      await downloadEcomSetZip(set.id, defaultZipName(set))
    } catch (err) {
      failWith(err, setActionError)
    } finally {
      setDownloading(false)
    }
  }

  // 主体区域：按 URL / 套图状态渲染对应步骤（含加载与错误态）
  function renderMain() {
    if (setId != null && !set) {
      return loadError ? (
        <div className="pf-ecom-section pf-ecom-center">
          <span className="pf-tool-error">{loadError}</span>
          <div className="pf-ecom-actions">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                currentIdRef.current = null
                setLoadKey((k) => k + 1)
              }}
            >
              {t('ecomSet.retry')}
            </Button>
            <Button variant="text" size="sm" onClick={() => backToForm()}>
              {t('ecomSet.result.newSet')}
            </Button>
          </div>
        </div>
      ) : (
        <LoadingBlock text={t('ecomSet.loading')} />
      )
    }
    if (set && step === 'plan') {
      return (
        <EcomSetPlan
          key={`${set.id}-${planVersion}`}
          set={set}
          billingEnabled={billingEnabled}
          busy={planBusy}
          error={actionError}
          onSave={onSavePlan}
          onGenerate={onGenerate}
          onReplan={onReplan}
        />
      )
    }
    if (set) {
      return (
        <>
          <EcomSetResults
            set={set}
            busyIdx={busyIdx}
            downloading={downloading}
            error={actionError}
            onPreview={setPreviewItem}
            onEditCopy={(item) => openModal('copy', item)}
            onRegenerate={(item) => openModal('regen', item)}
            onRetry={onRetry}
            onDownload={onDownload}
            onNew={() => backToForm()}
          />
          {canMakeDaihuoVideo(set) ? <DaihuoVideoPanel key={set.id} set={set} /> : null}
        </>
      )
    }
    if (optionsError) {
      return (
        <div className="pf-ecom-section pf-ecom-center">
          <span className="pf-tool-error">{optionsError}</span>
          <Button variant="outline" size="sm" onClick={() => setOptionsKey((k) => k + 1)}>
            {t('ecomSet.retry')}
          </Button>
        </div>
      )
    }
    if (!options) return <LoadingBlock text={t('ecomSet.loading')} />
    return (
      <EcomSetForm
        options={options}
        initial={formInitial}
        busy={formBusy}
        error={formError}
        onSubmit={onPlan}
      />
    )
  }

  const steps = m.ecomSet.steps.map((label, i) => ({ key: String(i), label }))
  const stepIndex = daihuoStepIndex(set)

  return (
    <AppShell active="kaikou">
      <div className="pf-tool-detail pf-ecom-page">
        <Link to="/studio/new" className="pf-tool-back">
          <ArrowLeft size={16} strokeWidth={2} aria-hidden />
          {t('daihuo.back')}
        </Link>
        <header className="pf-tool-detail-head">
          <span className="pf-ws-tool-icon" aria-hidden>
            <ShoppingBag size={22} strokeWidth={1.6} />
          </span>
          <div>
            <h1>{t('daihuo.title')}</h1>
            <p className="pf-muted">{t('daihuo.lead')}</p>
          </div>
        </header>
        <div className="pf-ecom-steps">
          <Stepper steps={steps} current={stepIndex} doneThrough={step === 'done' ? stepIndex : undefined} />
        </div>

        {renderMain()}

        <EcomSetHistory currentId={set?.id ?? null} refreshKey={historyKey} onOpen={(id) => setParams({ id: String(id) })} />
      </div>

      {copyItem ? (
        <EcomSetCopyModal
          key={`copy-${copyItem.idx}`}
          item={copyItem}
          language={set?.language}
          busy={modalBusy}
          error={modalError}
          onClose={() => setCopyItem(null)}
          onSave={onCopySave}
        />
      ) : null}
      {regenItem && set ? (
        <EcomSetRegenModal
          key={`regen-${regenItem.idx}`}
          item={regenItem}
          imageFen={set.image_fen}
          billingEnabled={billingEnabled}
          busy={modalBusy}
          error={modalError}
          onClose={() => setRegenItem(null)}
          onConfirm={onRegenConfirm}
        />
      ) : null}
      {previewItem ? <EcomSetPreviewModal item={previewItem} onClose={() => setPreviewItem(null)} /> : null}
    </AppShell>
  )
}

/** 区块加载态 */
function LoadingBlock({ text }: { text: string }) {
  return (
    <div className="pf-ecom-section pf-ecom-center">
      <Loader2 size={24} className="pf-tool-spin" aria-hidden />
      {text}
    </div>
  )
}
