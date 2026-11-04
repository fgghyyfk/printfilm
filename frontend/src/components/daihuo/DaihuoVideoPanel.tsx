import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Clapperboard, Loader2 } from 'lucide-react'
import { api, resolveVoiceId, type VoicePreset } from '../../api'
import { createDaihuoProject, quoteDaihuoProject, type DaihuoVideoQuote, type DaihuoRatio, type EcomSet } from '../../api/ecomSet'
import { useVoicePreview } from '../../hooks/useVoicePreview'
import { dialog } from '../../lib/dialog'
import { useI18n } from '../../i18n'
import { DAIHUO_RATIOS } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import './daihuo.css'

/** 音色卡片与后端共用的 speaker 键（与风格配置页一致） */
function voiceKey(v: VoicePreset): string {
  return v.speaker || v.id
}

/** 第⑤步：复用套图生成口播分镜，继续最近项目或明确新建另一条。 */
export default function DaihuoVideoPanel({ set }: { set: EcomSet }) {
  const { t } = useI18n()
  const nav = useNavigate()

  /* 视频设置：ratio 画幅；voices 音色列表；voiceId 选中音色；voicesError 音色加载失败 */
  const [ratio, setRatio] = useState<DaihuoRatio>(DAIHUO_RATIOS[0])
  const [voices, setVoices] = useState<VoicePreset[] | null>(null)
  const [voiceId, setVoiceId] = useState('')
  const [voicesError, setVoicesError] = useState('')

  /* 提交态：busy 创建/开写中；error 失败提示；projectId 已关联项目（含本次刚建好的） */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [projectId, setProjectId] = useState<number | null>(set.project_id)
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
      quoteDaihuoProject(set.id, { output_ratio: ratio, voice_id: voiceId }, controller.signal)
        .then((next) => { if (!controller.signal.aborted) setQuote(next) })
        .catch((err) => { if (!controller.signal.aborted) setQuoteError(err instanceof Error ? err.message : '估价失败') })
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [set.id, ratio, voiceId, projectId, createNew])

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
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    let pid: number | null = null
    try {
      stopPreview()
      const options = `${ratio}:${voiceId}`
      if (createNew && requestRef.current?.options !== options) {
        const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
        requestRef.current = { options, id }
      }
      const payload = { output_ratio: ratio, voice_id: voiceId, create_new: createNew,
        request_id: createNew ? requestRef.current!.id : undefined }
      const estimate = quote?.output_ratio === ratio && quote.voice_id === voiceId ? quote : await quoteDaihuoProject(set.id, payload)
      const confirmed = await dialog.confirm({ title: '生成带货口播分镜',
        message: `复用 ${estimate.usable_images} 张底图，新增生图费用 ¥0。写稿预估 ¥${(estimate.script_fen / 100).toFixed(2)}；后续配音预估 ¥${(estimate.tts_fen / 100).toFixed(2)}。本次先生成口播分镜，后续按阶段继续并预扣，按实际服务商用量结算。`,
        confirmText: '开始写稿',
      })
      if (!confirmed) return
      pid = (await createDaihuoProject(set.id, payload)).project_id
      setProjectId(pid)
      setCreateNew(false)
      requestRef.current = null
      await api.generate(pid)
      nav(`/studio/${pid}`)
    } catch (err) {
      if (pid) setProjectId(pid)
      setError(err instanceof Error ? err.message : t('daihuo.video.failed'))
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  if (projectId && !createNew) {
    return (
      <section className="pf-ecom-section pf-daihuo-panel">
        <div className="pf-ecom-section-head">
          <div>
            <h2>{t('daihuo.video.linkedTitle')}</h2>
            <p className="pf-muted">最近创建的视频可继续制作；新建另一条会保留原视频和作品。</p>
          </div>
          <div className="pf-ecom-actions">
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => { stopPreview(); requestRef.current = null; setError(''); setCreateNew(true) }}>新建另一个视频</Button>
            <Button variant="lime" size="sm" icon onClick={() => nav(`/studio/${projectId}`)}>
              {t('daihuo.video.view')}
              <ArrowRight size={14} aria-hidden />
            </Button>
          </div>
        </div>
        {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
      </section>
    )
  }

  return (
    <section className="pf-ecom-section pf-daihuo-panel">
      <div className="pf-ecom-section-head">
        <div>
          <h2>{createNew ? '使用同一套图新建另一个视频' : t('daihuo.video.title')}</h2>
          <p className="pf-muted">{t('daihuo.video.hint')}</p>
        </div>
      </div>

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
          {previewBusy === voiceId ? '试听生成中…' : playingId === voiceId ? '停止试听' : '试听当前音色'}
        </Button>
        {isFallback ? <span role="status">本次试听使用备用音色</span> : null}
      </div>
      <p className="pf-muted" role="status">{quote ? `复用 ${quote.usable_images} 张底图 ¥0；写稿预估 ¥${(quote.script_fen / 100).toFixed(2)}；后续配音预估 ¥${(quote.tts_fen / 100).toFixed(2)}` : quoteError || '正在估价…'}</p>
      <p className="pf-muted">先生成口播分镜，后续按阶段继续。配音为预估，按实际服务商用量结算。</p>
      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}

      <div className="pf-ecom-actions pf-daihuo-submit">
        {createNew ? <Button variant="ghost" disabled={busy} onClick={() => { stopPreview(); setCreateNew(false) }}>返回已有视频</Button> : null}
        <Button variant="lime" icon disabled={busy || voices == null || !quote} onClick={onCreate}>
          {busy ? <Loader2 size={16} className="pf-tool-spin" aria-hidden /> : <Clapperboard size={16} aria-hidden />}
          {busy ? t('daihuo.video.creating') : '生成口播分镜'}
        </Button>
      </div>
    </section>
  )
}
