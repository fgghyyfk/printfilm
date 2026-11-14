import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Clapperboard, Loader2 } from 'lucide-react'
import { api, resolveVoiceId, type VoicePreset } from '../../api'
import { createDaihuoProject, quoteDaihuoProject, type DaihuoVideoForm, type DaihuoVideoQuote, type DaihuoRatio, type EcomSet } from '../../api/ecomSet'
import { createJobVideo, quoteJobVideo, startDaihuoScript } from '../../api/ecomHub'
import { useVoicePreview } from '../../hooks/useVoicePreview'
import { dialog } from '../../lib/dialog'
import { useI18n } from '../../i18n'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { DAIHUO_RATIOS } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import './daihuo.css'

/** 视频形式选项，顺序即展示顺序 */
const VIDEO_FORMS: DaihuoVideoForm[] = ['static', 'mixed', 'motion']

/** 分 → 元（两位小数） */
function yuan(fen: number | undefined): string {
  return ((fen || 0) / 100).toFixed(2)
}

/** 音色卡片与后端共用的 speaker 键（与风格配置页一致） */
function voiceKey(v: VoicePreset): string {
  return v.speaker || v.id
}

/** 第⑤步：复用套图生成口播分镜，继续最近项目或明确新建另一条。 */
export default function DaihuoVideoPanel({ set, job }: { set: EcomSet; job?: never } | { set?: never; job: { id: number; project_id: number | null; title: string } }) {
  // 两种图片来源共用画幅、音色、报价和分阶段写稿流程。
  const source = job || set
  const quoteVideo = job ? quoteJobVideo : quoteDaihuoProject
  const createVideo = job ? createJobVideo : createDaihuoProject
  const { t , t: translateUi } = useI18n()
  const nav = useNavigate()

  /* 视频设置：ratio 画幅；voices 音色列表；voiceId 选中音色；voicesError 音色加载失败 */
  const [ratio, setRatio] = useState<DaihuoRatio>(DAIHUO_RATIOS[0])
  const [videoForm, setVideoForm] = useState<DaihuoVideoForm>('static')
  const [voices, setVoices] = useState<VoicePreset[] | null>(null)
  const [voiceId, setVoiceId] = useState('')
  const [voicesError, setVoicesError] = useState('')
  /* 批量结果没有商品规划，写稿前补充品名和卖点。 */
  const [name, setName] = useState(job?.title || '')
  const [notes, setNotes] = useState('')

  /* 提交态：busy 创建/开写中；error 失败提示；projectId 已关联项目（含本次刚建好的） */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [projectId, setProjectId] = useState<number | null>(source.project_id)
  const [createNew, setCreateNew] = useState(false)
  const submitting = useRef(false)
  const requestRef = useRef<{ options: string; id: string } | null>(null)
  const [quote, setQuote] = useState<DaihuoVideoQuote | null>(null)
  const [quoteError, setQuoteError] = useState('')
  const { previewBusy, playingId, isFallback, previewVoice, stopPreview } = useVoicePreview(setError)

  useEffect(() => {
    if (projectId && !createNew) return
    const controller = new AbortController()
    setQuote(null)
    setQuoteError('')
    const timer = setTimeout(() => {
      quoteVideo(source.id, { output_ratio: ratio, voice_id: voiceId, video_form: videoForm }, controller.signal)
        .then((next) => { if (!controller.signal.aborted) setQuote(next) })
        .catch((err) => { if (!controller.signal.aborted) setQuoteError(err instanceof Error ? err.message : translateUi("workflow.couldNotEstimateTheCost")) })
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [source.id, ratio, voiceId, videoForm, projectId, createNew, quoteVideo, translateUi])

  // 初次创建或明确新建时加载音色列表，默认选第一个
  useEffect(() => {
    if (projectId && !createNew) return
    let cancelled = false
    api
      .voices()
      .then((list) => {
        if (cancelled) return
        setVoices(list)
        setVoiceId((prev) => prev || resolveVoiceId('', list))
      })
      .catch((err) => {
        if (cancelled) return
        setVoices([])
        setVoicesError(err instanceof Error ? err.message : t('daihuo.video.voicesFailed'))
      })
    return () => {
      cancelled = true
    }
  }, [projectId, createNew, t])

  // 建项目 → 开始写带货口播 → 跳转分镜页
  async function onCreate() {
    if (submitting.current || (job && !name.trim())) return
    submitting.current = true
    setBusy(true)
    setError('')
    let pid: number | null = null
    try {
      stopPreview()
      const options = JSON.stringify([ratio, voiceId, videoForm, name.trim(), notes.trim()])
      if (createNew && requestRef.current?.options !== options) {
        const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
        requestRef.current = { options, id }
      }
      const payload = { output_ratio: ratio, voice_id: voiceId, video_form: videoForm, create_new: createNew,
        request_id: createNew ? requestRef.current!.id : undefined, ...(job ? { name: name.trim(), notes: notes.trim() } : {}) }
      const sameQuote = quote?.output_ratio === ratio && quote.voice_id === voiceId && (quote.video_form || 'static') === videoForm
      const estimate = sameQuote && quote ? quote : await quoteVideo(source.id, payload)
      const confirmed = await dialog.confirm({ title: translateUi("workflow.createProductNarrationStoryboards"),
        message: videoForm === 'static'
          ? translateUi("workflow.reuseBaseImagesWith0InNewImageCharges", { v1: String(estimate.usable_images), v2: yuan(estimate.script_fen), v3: yuan(estimate.tts_fen) })
          : t('daihuo.video.confirmMotion', { images: String(estimate.usable_images), shots: String(estimate.motion_shots || 0),
            script: yuan(estimate.script_fen), tts: yuan(estimate.tts_fen), video: yuan(estimate.video_fen),
            image: estimate.image_fen ? t('daihuo.video.quoteOutpaint', { fen: yuan(estimate.image_fen) }) : '' }),
        confirmText: translateUi("workflow.startWriting"),
      })
      if (!confirmed) return
      pid = (await createVideo(source.id, payload)).project_id
      setProjectId(pid)
      setCreateNew(false)
      requestRef.current = null
      await startDaihuoScript(pid)
      nav(`/studio/${pid}`)
    } catch (err) {
      if (pid) setProjectId(pid)
      setError(err instanceof Error ? err.message : t('daihuo.video.failed'))
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  /* 动态展示前置条件不满足（未开 OSS 且禁止内嵌）时禁止提交 */
  const motionBlocked = videoForm !== 'static' && quote?.motion_available === false

  /** 报价行：静图沿用原文案；动态展示分项列出视频与扩图 */
  function quoteText(): string {
    if (!quote) return quoteError ? ecomJobErrorText(quoteError, t) : translateUi("workflow.estimatingCost")
    if (motionBlocked) return quote.motion_unavailable_reason || ''
    if ((quote.video_form || 'static') === 'static') {
      return translateUi("workflow.reuseImages0ScriptEstimateLaterNarrationEstimate", { v1: String(quote.usable_images), v2: yuan(quote.script_fen), v3: yuan(quote.tts_fen) })
    }
    return t('daihuo.video.quoteMotion', { script: yuan(quote.script_fen), tts: yuan(quote.tts_fen), shots: String(quote.motion_shots || 0),
      video: yuan(quote.video_fen), image: quote.image_fen ? t('daihuo.video.quoteOutpaint', { fen: yuan(quote.image_fen) }) : '',
      total: yuan(quote.estimate_fen) })
  }

  if (projectId && !createNew) {
    return (
      <section className="pf-ecom-section pf-daihuo-panel">
        <div className="pf-ecom-section-head">
          <div>
            <h2>{t('daihuo.video.linkedTitle')}</h2>
            <p className="pf-muted">{translateUi("workflow.continueYourLatestVideoOrCreateAnotherWhileKeeping")}</p>
          </div>
          <div className="pf-ecom-actions">
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => { stopPreview(); requestRef.current = null; setError(''); setCreateNew(true) }}>{translateUi("workflow.createAnotherVideo")}</Button>
            <Button variant="lime" size="sm" icon onClick={() => nav(`/studio/${projectId}`)}>
              {t('daihuo.video.view')}
              <ArrowRight size={14} aria-hidden />
            </Button>
          </div>
        </div>
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
      </section>
    )
  }

  return (
    <section className="pf-ecom-section pf-daihuo-panel">
      <div className="pf-ecom-section-head">
        <div>
          <h2>{createNew ? translateUi("workflow.createAnotherVideoFromThisImageSet") : t('daihuo.video.title')}</h2>
          {/* 动态展示可能扩图，通用提示里的「无需额外生图」只对静图口播成立 */}
          {videoForm === 'static' ? <p className="pf-muted">{t('daihuo.video.hint')}</p> : null}
        </div>
      </div>

      {job ? <>
        <label className="pf-tool-field"><span>{t('ecomSet.form.name')}</span><input value={name} maxLength={128} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
        <label className="pf-tool-field"><span>{t('ecomSet.form.notes')}</span><textarea value={notes} maxLength={500} disabled={busy} onChange={(event) => setNotes(event.target.value)} /></label>
      </> : null}
      <div className="pf-tool-field">
        <span>{t('daihuo.video.ratio')}</span>
        <div className="pf-tool-chips">
          {DAIHUO_RATIOS.map((r) => (
            <button
              key={r}
              type="button"
              disabled={busy}
              className={ratio === r ? 'active' : ''}
              onClick={() => setRatio(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <div className="pf-tool-field">
        <span>{t('daihuo.video.form.label')}</span>
        <div className="pf-tool-chips" role="radiogroup" aria-label={t('daihuo.video.form.label')}>
          {VIDEO_FORMS.map((form) => (
            <button key={form} type="button" role="radio" aria-checked={videoForm === form} disabled={busy}
              className={videoForm === form ? 'active' : ''} onClick={() => setVideoForm(form)}>
              {t(`daihuo.video.form.${form}`)}
              {form === 'mixed' ? ` · ${t('daihuo.video.form.recommended')}` : ''}
            </button>
          ))}
        </div>
        <span className="pf-muted">{t(`daihuo.video.form.${videoForm}Hint`)}</span>
      </div>

      <div className="pf-tool-field">
        <span>{t('daihuo.video.voice')}</span>
        {voices == null ? (
          <span className="pf-muted">{t('daihuo.video.voicesLoading')}</span>
        ) : voices.length ? (
          <div className="pf-tool-chips">
            {voices.map((v) => (
              <button
                key={v.id}
                type="button"
                disabled={busy}
                className={voiceId === voiceKey(v) ? 'active' : ''}
                onClick={() => { stopPreview(); setVoiceId(voiceKey(v)) }}
              >
                {v.label}
                {v.gender === 'female' || v.gender === 'male' ? ` · ${t(`daihuo.video.gender.${v.gender}`)}` : ''}
              </button>
            ))}
          </div>
        ) : (
          <span className="pf-muted">{voicesError || t('daihuo.video.voicesEmpty')}</span>
        )}
      </div>

      <div className="pf-ecom-actions">
        <Button variant="ghost" size="sm" disabled={!voiceId || busy || previewBusy === voiceId}
          onClick={() => { setError(''); void previewVoice(voiceId, 'daihuo') }}>
          {previewBusy === voiceId ? translateUi("workflow.preparingPreview") : playingId === voiceId ? translateUi("workflow.stopPreview") : translateUi("workflow.previewSelectedVoice")}
        </Button>
        {isFallback ? <span role="status">{translateUi("workflow.thisPreviewUsesABackupVoice")}</span> : null}
      </div>
      <p className="pf-muted" role="status">{quoteText()}</p>
      <p className="pf-muted">{translateUi("workflow.startWithNarrationStoryboardsThenContinueByStageNarration")}</p>
      {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}

      <div className="pf-ecom-actions pf-daihuo-submit">
        {createNew ? <Button variant="ghost" disabled={busy} onClick={() => { stopPreview(); setCreateNew(false) }}>{translateUi("workflow.backToExistingVideo")}</Button> : null}
        <Button variant="lime" icon disabled={busy || voices == null || !quote || motionBlocked || Boolean(job && !name.trim())} onClick={onCreate}>
          {busy ? <Loader2 size={16} className="pf-tool-spin" aria-hidden /> : <Clapperboard size={16} aria-hidden />}
          {busy ? t('daihuo.video.creating') : translateUi("workflow.createNarrationStoryboards")}
        </Button>
      </div>
    </section>
  )
}
