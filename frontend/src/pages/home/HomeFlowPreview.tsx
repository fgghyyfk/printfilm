/** 首页流程区右侧示意：按当前步骤展示这一步实际产出的内容（纯装饰，不读屏）。 */
import { Check, Play } from 'lucide-react'
import { useI18n } from '../../i18n'

/* 波形柱高（%），固定值保证每次渲染一致 */
const WAVE = [28, 52, 74, 40, 88, 62, 34, 70, 96, 58, 30, 66, 84, 46, 24, 60, 78, 38, 54, 90, 44, 26]

/** @param step 当前流程步骤：0 上传 / 1 写稿 / 2 配音 / 3 成片 */
export default function HomeFlowPreview({ step }: { step: number }) {
  const { t, m } = useI18n()
  const p = m.home.flowPreview

  return (
    <div className="kk-preview" aria-hidden>
      {step === 0 && (
        <div className="kk-pv-upload">
          <figure>
            <img src="/char-presets/real.jpg" alt="" width={132} height={176} loading="lazy" decoding="async" />
            <i className="kk-pv-face" />
            <figcaption>{p.photo}</figcaption>
          </figure>
          <ul>
            {p.checks.map((c) => (
              <li key={c}>
                <Check size={15} strokeWidth={2.6} />
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {step === 1 && (
        <div className="kk-pv-script">
          <span className="kk-pv-label">{p.sample}</span>
          {p.segments.map((text, i) => (
            <p key={text}>
              <b>{t('home.flowPreview.segmentLabel', { n: i + 1 })}</b>
              {text}
            </p>
          ))}
        </div>
      )}
      {step === 2 && (
        <div className="kk-pv-voice">
          <div className="kk-pv-chips">
            {p.voices.map((v, i) => (
              <span key={v} className={i === 0 ? 'is-on' : ''}>
                {v}
              </span>
            ))}
          </div>
          <div className="kk-pv-wave">
            {WAVE.map((h, i) => (
              <i key={i} style={{ height: `${h}%`, animationDelay: `${i * 60}ms` }} />
            ))}
          </div>
          <span className="kk-pv-label">{p.voiceNow}</span>
        </div>
      )}
      {step === 3 && (
        <div className="kk-pv-final">
          <div className="kk-phone">
            <img src="/char-presets/real.jpg" alt="" width={130} height={231} loading="lazy" decoding="async" />
            <span className="kk-pv-play">
              <Play size={16} fill="currentColor" strokeWidth={0} />
            </span>
            <div className="kk-phone-title">
              <span className="is-top">{t('home.winTitleTop')}</span>
              <span>{t('home.winTitleBottom')}</span>
            </div>
          </div>
          <ul>
            {p.outputs.map((o) => (
              <li key={o}>
                <Check size={15} strokeWidth={2.6} />
                {o}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
