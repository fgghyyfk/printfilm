import type { EcomKindOptions, RefineParams } from '../../api/ecomJobs'
import { useI18n } from '../../i18n'

type Props = { value: RefineParams; options: EcomKindOptions; disabled: boolean; onChange: (value: RefineParams) => void }

/** 精修参数面板：背景、比例、清晰度和可选补充要求。 */
export default function ParameterPanel({ value, options, disabled, onChange }: Props) {
  const { t } = useI18n()
  return (
    <div className="pf-job-params">
      <label>{t('ecomJobs.background')}
        <select disabled={disabled} value={value.background} onChange={(event) => onChange({ ...value, background: event.target.value as RefineParams['background'] })}>
          {(['white', 'keep', 'transparent'] as const).map((key) => <option key={key} value={key}>{t(`ecomJobs.backgrounds.${key}`)}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.ratio')}
        <select disabled={disabled} value={value.ratio} onChange={(event) => onChange({ ...value, ratio: event.target.value as RefineParams['ratio'] })}>
          {options.ratios.map((ratio) => <option key={ratio} value={ratio}>{ratio === '原比例' ? t('ecomJobs.originalRatio') : ratio}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.clarity')}
        <select disabled={disabled} value={value.clarity} onChange={(event) => onChange({ ...value, clarity: event.target.value as RefineParams['clarity'] })}>
          {options.clarities.map((clarity) => <option key={clarity} value={clarity}>{clarity.toUpperCase()}</option>)}
        </select>
      </label>
      <label className="pf-job-notes">{t('ecomJobs.notes')}
        <textarea disabled={disabled} rows={3} maxLength={500} value={value.notes} placeholder={t('ecomJobs.notesHint')} onChange={(event) => onChange({ ...value, notes: event.target.value })} />
      </label>
      <p className="pf-muted pf-job-notes">{t('ecomJobs.transparentHint')}</p>
    </div>
  )
}
