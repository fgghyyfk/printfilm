import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { translateEcomSet, type EcomSet, type EcomSetOptions } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import { ecomJobErrorText } from '../../lib/ecomJobs'
import { formatFen } from '../../lib/ecomSet'
import { canTranslateSet, translationTargets, type TranslationLanguage } from '../../lib/ecomTranslate'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'

/* 仅完成套图提供翻译入口，价格由套图选项统一下发。 */
type Props = { set: EcomSet; options: EcomSetOptions | null; optionsError: string; onReloadOptions: () => void; onTranslated: (set: EcomSet) => void }

/** 翻译已完成套图的文案，确认文本费用并跳转到新套图。 */
export default function EcomSetTranslate({ set, options, optionsError, onReloadOptions, onTranslated }: Props) {
  const { t } = useI18n()
  const targets = translationTargets(set.language)
  /* language 为目标语言；busy/error 为提交状态；active 拦截离开当前套图后的迟到响应。 */
  const [language, setLanguage] = useState<TranslationLanguage>(targets[0])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(true)
  // 卸载当前套图后停止导航和状态更新。
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  // 收费时先确认，沿用原底图生成新套图，不修改当前套图。
  async function translate() {
    if (!options || busy) return
    if (options.billing_enabled && !await dialog.confirm({
      title: t('ecomSet.translate.confirmTitle'),
      message: t('ecomSet.translate.confirm', { language: t(`ecomSet.enhanced.languages.${language}`), price: formatFen(options.translate_fen) }),
      confirmText: t('ecomSet.translate.start'),
    })) return
    if (!active.current) return
    setBusy(true)
    setError('')
    try {
      const translated = await translateEcomSet(set.id, language)
      if (active.current) onTranslated(translated)
    } catch (err) {
      if (active.current) setError(err instanceof Error ? err.message : t('ecomSet.translate.failed'))
    } finally {
      if (active.current) setBusy(false)
    }
  }

  return (
    <section className="pf-ecom-section">
      {set.source_set_id ? <p className="pf-muted">{t('ecomSet.translate.source', { id: set.source_set_id })} · <Link to={`/daihuo?id=${set.source_set_id}`}>{t('ecomSet.translate.sourceLink')}</Link></p> : null}
      {canTranslateSet(set) ? <>
        <h2>{t('ecomSet.translate.title')}</h2>
        <p className="pf-muted">{t('ecomSet.translate.hint')}</p>
        <div className="pf-job-params">
          <label>{t('ecomSet.translate.language')}<select value={language} disabled={busy} onChange={(event) => setLanguage(event.target.value as TranslationLanguage)}>
            {targets.map((target) => <option key={target} value={target}>{t(`ecomSet.enhanced.languages.${target}`)}</option>)}
          </select></label>
        </div>
        {optionsError && !options ? <div role="alert"><p className="pf-tool-error">{ecomJobErrorText(optionsError, t)}</p><Button type="button" variant="outline" onClick={onReloadOptions}>{t('ecomSet.retry')}</Button></div> : null}
        {error ? <BillingErrorNotice message={ecomJobErrorText(error, t)} className="pf-tool-error" /> : null}
        <div className="pf-ecom-actions">
          <Button type="button" variant="outline" disabled={busy || !options} onClick={translate}>{t(busy ? 'ecomSet.translate.working' : 'ecomSet.translate.start')}</Button>
          {options ? options.billing_enabled ? <span className="pf-muted">{t('ecomSet.translate.price', { price: formatFen(options.translate_fen) })}</span> : null : !optionsError ? <span role="status" className="pf-muted">{t('ecomSet.translate.loading')}</span> : null}
        </div>
      </> : null}
    </section>
  )
}
