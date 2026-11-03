import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Clapperboard, Loader2 } from 'lucide-react'
import { api, resolveVoiceId, type VoicePreset } from '../../api'
import { createDaihuoProject, type DaihuoRatio, type EcomSet } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { DAIHUO_RATIOS } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import './daihuo.css'

/** 音色卡片与后端共用的 speaker 键（与风格配置页一致） */
function voiceKey(v: VoicePreset): string {
  return v.speaker || v.id
}

/** 第⑤步：用套图一键生成带货短视频；已生成过则直接跳转分镜页 */
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

  // 未关联项目时加载音色列表，默认选第一个
  useEffect(() => {
    if (projectId) return
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
  }, [projectId, t])

  // 建项目 → 开始写带货口播 → 跳转分镜页
  async function onCreate() {
    setBusy(true)
    setError('')
    let pid: number | null = null
    try {
      pid = (await createDaihuoProject(set.id, { output_ratio: ratio, voice_id: voiceId || undefined })).project_id
      await api.generate(pid)
      nav(`/studio/${pid}`)
    } catch (err) {
      if (pid) setProjectId(pid)
      setError(err instanceof Error ? err.message : t('daihuo.video.failed'))
    } finally {
      setBusy(false)
    }
  }

  if (projectId) {
    return (
      <section className="pf-ecom-section pf-daihuo-panel">
        <div className="pf-ecom-section-head">
          <div>
            <h2>{t('daihuo.video.linkedTitle')}</h2>
            <p className="pf-muted">{t('daihuo.video.linkedHint')}</p>
          </div>
          <Button variant="lime" size="sm" icon onClick={() => nav(`/studio/${projectId}`)}>
            {t('daihuo.video.view')}
            <ArrowRight size={14} aria-hidden />
          </Button>
        </div>
        {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
      </section>
    )
  }

  return (
    <section className="pf-ecom-section pf-daihuo-panel">
      <div className="pf-ecom-section-head">
        <div>
          <h2>{t('daihuo.video.title')}</h2>
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
                onClick={() => setVoiceId(voiceKey(v))}
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

      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}

      <div className="pf-ecom-actions pf-daihuo-submit">
        <Button variant="lime" icon disabled={busy || voices == null} onClick={onCreate}>
          {busy ? <Loader2 size={16} className="pf-tool-spin" aria-hidden /> : <Clapperboard size={16} aria-hidden />}
          {busy ? t('daihuo.video.creating') : t('daihuo.video.create')}
        </Button>
      </div>
    </section>
  )
}
