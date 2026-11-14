import type { EcomLanguage, EcomSetCopyPatch } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { copyLimits, ECOM_COPY_LIMITS, padBullets } from '../../lib/ecomSet'

type Props = {
  value: EcomSetCopyPatch
  onChange: (next: EcomSetCopyPatch) => void
  disabled?: boolean
  /** 主标题字数上限（默认 ECOM_COPY_LIMITS.title） */
  titleMax?: number
  /** 套图目标语言：决定主/副标题与卖点的字数上限 */
  language?: EcomLanguage
}

/** 文案编辑字段：主标题 / 副标题 / 英文小标 / 最多 3 条卖点（方案卡片与改文案弹层共用） */
export default function EcomSetCopyFields({ value, onChange, disabled, titleMax, language }: Props) {
  const { t } = useI18n()
  // 按语言取上限；版式放宽的主标题（如日化封面 16 字）与语言上限取较大者
  const limits = copyLimits(language)
  const titleLimit = Math.max(titleMax ?? 0, limits.title)
  const bullets = padBullets(value.bullets)

  // 修改第 n 条卖点
  function setBullet(index: number, text: string) {
    const next = bullets.slice()
    next[index] = text
    onChange({ ...value, bullets: next })
  }

  return (
    <>
      <label className="pf-tool-field">
        <span className="pf-ecom-field-label">
          {t('ecomSet.plan.copyTitle')}
          <em>
            {value.title.length}/{titleLimit}
          </em>
        </span>
        <input
          className="pf-ecom-input"
          type="text"
          maxLength={titleLimit}
          value={value.title}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
        />
      </label>
      <label className="pf-tool-field">
        <span className="pf-ecom-field-label">
          {t('ecomSet.plan.subtitle')}
          <em>
            {value.subtitle.length}/{limits.subtitle}
          </em>
        </span>
        <input
          className="pf-ecom-input"
          type="text"
          maxLength={limits.subtitle}
          value={value.subtitle}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, subtitle: e.target.value })}
        />
      </label>
      <label className="pf-tool-field">
        <span className="pf-ecom-field-label">
          {t('ecomSet.plan.en')}
          <em>
            {(value.en || '').length}/{ECOM_COPY_LIMITS.en}
          </em>
        </span>
        <input
          className="pf-ecom-input"
          type="text"
          maxLength={ECOM_COPY_LIMITS.en}
          placeholder="Soft & Fragrant"
          value={value.en || ''}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, en: e.target.value })}
        />
      </label>
      <div className="pf-tool-field">
        <span className="pf-ecom-field-label">{t('ecomSet.plan.bullets')}</span>
        <div className="pf-ecom-bullets">
          {bullets.map((bullet, index) => (
            <input
              key={index}
              className="pf-ecom-input"
              type="text"
              maxLength={limits.bullet}
              placeholder={t('ecomSet.plan.bulletPlaceholder', { n: index + 1 })}
              value={bullet}
              disabled={disabled}
              onChange={(e) => setBullet(index, e.target.value)}
            />
          ))}
        </div>
      </div>
    </>
  )
}
