import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { EcomSet, EcomSetItem } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { ecomSetTotalFen, formatFen, hasCopy, isPlanDirty } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import EcomSetCopyFields from './EcomSetCopyFields'
import EcomSetWarnings from './EcomSetWarnings'

/** 当前进行中的方案操作 */
export type EcomPlanBusy = 'save' | 'generate' | null

type Props = {
  set: EcomSet
  billingEnabled: boolean
  busy: EcomPlanBusy
  error: string
  onSave: (items: EcomSetItem[]) => void
  onGenerate: (items: EcomSetItem[], dirty: boolean) => void
  onReplan: () => void
}

/** 第②步：方案卡片网格，可改画面与文案，保存 / 确认生成 / 重新规划 */
export default function EcomSetPlan({ set, billingEnabled, busy, error, onSave, onGenerate, onReplan }: Props) {
  const { t } = useI18n()
  /* draft 编辑稿（父组件以 set 版本作 key 重置） */
  const [draft, setDraft] = useState<EcomSetItem[]>(() => set.items.map((item) => ({ ...item })))
  const [localError, setLocalError] = useState('')
  const dirty = isPlanDirty(set.items, draft)
  const total = formatFen(ecomSetTotalFen(set))

  // 修改某张的字段
  function patchItem(idx: number, patch: Partial<EcomSetItem>) {
    setDraft((prev) => prev.map((item) => (item.idx === idx ? { ...item, ...patch } : item)))
  }

  // 校验画面描述非空；返回是否通过
  function validate(): boolean {
    const empty = draft.findIndex((item) => !item.scene.trim())
    if (empty >= 0) {
      setLocalError(t('ecomSet.plan.sceneRequired', { n: empty + 1 }))
      return false
    }
    setLocalError('')
    return true
  }

  const shownError = localError || error

  return (
    <section className="pf-ecom-section">
      <div className="pf-ecom-section-head">
        <div>
          <h2>{t('ecomSet.plan.title')}</h2>
          <p className="pf-muted">{t('ecomSet.plan.lead')}</p>
        </div>
        <div className="pf-ecom-actions">
          {dirty ? <span className="pf-badge run">{t('ecomSet.plan.unsaved')}</span> : null}
          <Button variant="text" size="sm" disabled={Boolean(busy)} onClick={onReplan}>
            {t('ecomSet.plan.replan')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!dirty || Boolean(busy)}
            onClick={() => validate() && onSave(draft)}
          >
            {busy === 'save' ? <Loader2 size={14} className="pf-tool-spin" aria-hidden /> : null}
            {t('ecomSet.plan.save')}
          </Button>
          <Button
            variant="lime"
            size="sm"
            disabled={Boolean(busy)}
            onClick={() => validate() && onGenerate(draft, dirty)}
          >
            {busy === 'generate' ? <Loader2 size={14} className="pf-tool-spin" aria-hidden /> : null}
            {billingEnabled ? t('ecomSet.plan.generateWithPrice', { price: total }) : t('ecomSet.plan.generate')}
          </Button>
        </div>
      </div>

      <EcomSetWarnings warnings={set.warnings} />
      {shownError ? <BillingErrorNotice message={shownError} className="pf-tool-error" /> : null}

      <div className="pf-ecom-grid" style={{ marginTop: shownError ? '0.75rem' : 0 }}>
        {draft.map((item) => (
          <article key={item.idx} className="pf-ecom-card">
            <div className="pf-ecom-card-head">
              <strong>
                {item.idx + 1}. {t(`ecomSet.enhanced.slots.${item.slot}.name`)}
              </strong>
              <span className="pf-badge">{item.ratio}</span>
            </div>
            <label className="pf-tool-field">
              <span className="pf-ecom-field-label">{t('ecomSet.plan.scene')}</span>
              <textarea
                rows={3}
                placeholder={t('ecomSet.plan.sceneHint')}
                value={item.scene}
                disabled={Boolean(busy)}
                onChange={(e) => patchItem(item.idx, { scene: e.target.value })}
              />
            </label>
            {hasCopy(item) ? (
              <EcomSetCopyFields
                language={set.language}
                value={{ title: item.title, subtitle: item.subtitle, bullets: item.bullets }}
                disabled={Boolean(busy)}
                onChange={(copy) => patchItem(item.idx, copy)}
              />
            ) : (
              <p className="pf-muted pf-tool-panel-hint">{t('ecomSet.plan.noCopy')}</p>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
