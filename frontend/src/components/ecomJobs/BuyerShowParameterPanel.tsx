import type { EcomKindOptions, BuyerShowParams } from '../../api/ecomJobs'
import { useI18n } from '../../i18n'

type Props = { value: BuyerShowParams; options: EcomKindOptions; disabled: boolean; onChange: (value: BuyerShowParams) => void }

/** 买家秀参数：生活风格、输出张数、画幅、清晰度和补充要求。 */
export default function BuyerShowParameterPanel({ value, options, disabled, onChange }: Props) {
  const { t } = useI18n()
  return (
    <div className="pf-job-params">
      <label>{t('ecomJobs.buyerShow.mode')}
        <select disabled={disabled} value={value.mode} onChange={(event) => onChange({ ...value, mode: event.target.value as BuyerShowParams['mode'] })}>
          {(['buyer', 'seeding'] as const).map((mode) => <option key={mode} value={mode}>{t(`ecomJobs.buyerShow.modes.${mode}`)}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.buyerShow.count')}
        <select disabled={disabled} value={value.count} onChange={(event) => onChange({ ...value, count: Number(event.target.value) })}>
          {Array.from({ length: options.max_count || 8 }, (_, i) => i + 1).map((count) => <option key={count} value={count}>{count}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.ratio')}
        <select disabled={disabled} value={value.ratio} onChange={(event) => onChange({ ...value, ratio: event.target.value as BuyerShowParams['ratio'] })}>
          {options.ratios.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.clarity')}
        <select disabled={disabled} value={value.clarity} onChange={(event) => onChange({ ...value, clarity: event.target.value as BuyerShowParams['clarity'] })}>
          {options.clarities.map((clarity) => <option key={clarity} value={clarity}>{clarity.toUpperCase()}</option>)}
        </select>
      </label>
      <label className="pf-job-notes">{t('ecomJobs.notes')}
        <textarea disabled={disabled} rows={3} maxLength={500} value={value.notes} placeholder={t('ecomJobs.buyerShow.notesHint')} onChange={(event) => onChange({ ...value, notes: event.target.value })} />
      </label>
      <p className="pf-muted pf-job-notes">{t('ecomJobs.buyerShow.referenceHint')}</p>
    </div>
  )
}
