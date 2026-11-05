import type { EcomKindOptions, SkuParams } from '../../api/ecomJobs'
import { useI18n } from '../../i18n'

type Props = { value: SkuParams; options: EcomKindOptions; disabled: boolean; onChange: (value: SkuParams) => void }

/** SKU 参数面板：服务端自动场景画幅、清晰度与合规区域描述。 */
export default function SkuParameterPanel({ value, options, disabled, onChange }: Props) {
  const { t } = useI18n()
  return (
    <div className="pf-job-params">
      <label>{t('ecomJobs.ratio')}
        <select disabled={disabled} value={value.ratio || ''} onChange={(event) => onChange({ ...value, ratio: (event.target.value || null) as SkuParams['ratio'] })}>
          <option value="">{t('ecomJobs.sku.autoRatio')}</option>
          {options.ratios.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
        </select>
      </label>
      <label>{t('ecomJobs.clarity')}
        <select disabled={disabled} value={value.clarity} onChange={(event) => onChange({ ...value, clarity: event.target.value as SkuParams['clarity'] })}>
          {options.clarities.map((clarity) => <option key={clarity} value={clarity}>{clarity.toUpperCase()}</option>)}
        </select>
      </label>
      <label className="pf-job-notes">{t('ecomJobs.notes')}
        <textarea disabled={disabled} rows={3} maxLength={500} value={value.notes} placeholder={t('ecomJobs.sku.notesHint')} onChange={(event) => onChange({ ...value, notes: event.target.value })} />
      </label>
      <p className="pf-muted pf-job-notes">{t('ecomJobs.sku.referenceHint')}</p>
    </div>
  )
}
