import type { EcomLanguage, EcomClarity, EcomSetOptions } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { formatFen } from '../../lib/ecomSet'

type Props = {
  options: EcomSetOptions
  slots: string[] | undefined
  language: EcomLanguage
  clarity: EcomClarity
  busy: boolean
  onSlots: (slots: string[] | undefined) => void
  onLanguage: (language: EcomLanguage) => void
  onClarity: (clarity: EcomClarity) => void
}

/** 自定义模块、目标语言和清晰度参数，复用现有表单样式。 */
export default function EcomSetParameters({ options, slots, language, clarity, busy, onSlots, onLanguage, onClarity }: Props) {
  const { t } = useI18n()
  return <>
    <label className="pf-tool-field">
      <span><input type="checkbox" checked={slots !== undefined} disabled={busy}
        onChange={(event) => onSlots(event.target.checked ? ['main_white', 'main_scene', 'usage'] : undefined)} /> {t('ecomSet.enhanced.custom')}</span>
    </label>
    {slots !== undefined ? <fieldset className="pf-tool-field" disabled={busy}>
      <legend>{t('ecomSet.enhanced.custom')} ({slots.length}/10)</legend>
      <p className="pf-muted">{t('ecomSet.enhanced.slotsHint')}</p>
      {options.slots.map((slot) => <label key={slot.id}>
        <input type="checkbox" checked={slots.includes(slot.id)}
          onChange={(event) => onSlots(event.target.checked ? [...slots, slot.id] : slots.filter((key) => key !== slot.id))} />
        {' '}{t(`ecomSet.enhanced.slots.${slot.id}.name`)} — {t(`ecomSet.enhanced.slots.${slot.id}.description`)}
      </label>)}
    </fieldset> : null}
    <label className="pf-tool-field">
      <span>{t('ecomSet.enhanced.language')}</span>
      <select value={language} disabled={busy} onChange={(event) => onLanguage(event.target.value as EcomLanguage)}>
        {options.languages.map((key) => <option value={key} key={key}>{t(`ecomSet.enhanced.languages.${key}`)}</option>)}
      </select>
    </label>
    <label className="pf-tool-field">
      <span>{t('ecomSet.enhanced.clarity')}</span>
      <select value={clarity} disabled={busy} onChange={(event) => onClarity(event.target.value as EcomClarity)}>
        {options.clarities.map((key) => <option value={key} key={key}>{key.toUpperCase()}</option>)}
      </select>
      <span className="pf-muted">{t('ecomSet.enhanced.clarityHint')}</span>
      {options.billing_enabled ? <span className="pf-ecom-price">{t('ecomSet.enhanced.price', { price: formatFen(options.prices[clarity]) })}</span> : null}
    </label>
  </>
}
