import type { EcomKindOptions, StyleParams } from '../../api/ecomJobs'
import { useI18n } from '../../i18n'

type Props = { value: StyleParams; options: EcomKindOptions; disabled: boolean; onChange: (value: StyleParams) => void }

/** 风格复刻参数：模式、单图输出张数、画幅、清晰度和补充要求。 */
export default function StyleParameterPanel({ value, options, disabled, onChange }: Props) {
  const { t } = useI18n()
  return (
    <div className="pf-job-params">
      <label>{t('ecomJobs.style.mode')}
        <select disabled={disabled} value={value.mode} onChange={(event) => onChange({ ...value, mode: event.target.value as StyleParams['mode'], count: 1 })}>
          {(['single', 'batch'] as const).map((mode) => <option key={mode} value={mode}>{t(`ecomJobs.style.modes.${mode}`)}</option>)}
        </select>
      </label>
      {value.mode === 'single' ? <label>{t('ecomJobs.style.count')}
        <select disabled={disabled} value={value.count} onChange={(event) => onChange({ ...value, count: Number(event.target.value) })}>
          {Array.from({ length: options.max_count || 4 }, (_, i) => i + 1).map((count) => <option key={count} value={count}>{count}</option>)}
        </select>
      </label> : null}
      <label>{t('ecomJobs.ratio')}
        <select disabled={disabled} value={value.ratio} onChange={(event) => onChange({ ...value, ratio: event.target.value as StyleParams['ratio'] })}>
          {options.ratios.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.clarity')}
        <select disabled={disabled} value={value.clarity} onChange={(event) => onChange({ ...value, clarity: event.target.value as StyleParams['clarity'] })}>
          {options.clarities.map((clarity) => <option key={clarity} value={clarity}>{clarity.toUpperCase()}</option>)}
        </select>
      </label>
      <p className="pf-muted pf-job-notes">{t(`ecomJobs.style.hints.${value.mode}`)}</p>
      <label className="pf-job-notes">{t('ecomJobs.notes')}
        <textarea disabled={disabled} rows={3} maxLength={500} value={value.notes} placeholder={t('ecomJobs.style.notesHint')} onChange={(event) => onChange({ ...value, notes: event.target.value })} />
      </label>
      <p className="pf-muted pf-job-notes">{t('ecomJobs.style.referenceHint')}</p>
    </div>
  )
}
