import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { EcomSetCopyPatch, EcomSetItem } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { useI18n } from '../../i18n'
import { formatFen } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import Modal from '../ui/Modal'
import EcomSetCopyFields from './EcomSetCopyFields'

type CopyProps = {
  item: EcomSetItem
  busy: boolean
  error: string
  onClose: () => void
  onSave: (copy: EcomSetCopyPatch) => void
}

/** 改文案弹层：编辑后 PUT copy，同步重新排版（免费）；父组件以 item 作 key 重置 */
export function EcomSetCopyModal({ item, busy, error, onClose, onSave }: CopyProps) {
  const { t } = useI18n()
  const [copy, setCopy] = useState<EcomSetCopyPatch>({
    title: item.title,
    subtitle: item.subtitle,
    bullets: item.bullets,
  })

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={t('ecomSet.copyModal.title', { label: item.slot_label })}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            {t('ecomSet.copyModal.cancel')}
          </Button>
          <Button variant="lime" disabled={busy} onClick={() => onSave(copy)}>
            {busy ? <Loader2 size={14} className="pf-tool-spin" aria-hidden /> : null}
            {busy ? t('ecomSet.copyModal.saving') : t('ecomSet.copyModal.save')}
          </Button>
        </>
      }
    >
      <p className="pf-muted pf-ecom-modal-hint">{t('ecomSet.copyModal.hint')}</p>
      <div className="pf-ecom-modal-fields">
        <EcomSetCopyFields value={copy} onChange={setCopy} disabled={busy} />
      </div>
      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
    </Modal>
  )
}

type RegenProps = {
  item: EcomSetItem
  imageFen: number
  billingEnabled: boolean
  busy: boolean
  error: string
  onClose: () => void
  /** scene 未改动时传 undefined */
  onConfirm: (scene: string | undefined) => void
}

/** 重新生成弹层：可改画面描述，按钮标价，确认即收一张生图费 */
export function EcomSetRegenModal({ item, imageFen, billingEnabled, busy, error, onClose, onConfirm }: RegenProps) {
  const { t } = useI18n()
  const [scene, setScene] = useState(item.scene)
  const trimmed = scene.trim()

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={t('ecomSet.regenModal.title', { label: item.slot_label })}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            {t('ecomSet.regenModal.cancel')}
          </Button>
          <Button
            variant="lime"
            disabled={busy || !trimmed}
            onClick={() => onConfirm(trimmed === item.scene.trim() ? undefined : trimmed)}
          >
            {busy ? <Loader2 size={14} className="pf-tool-spin" aria-hidden /> : null}
            {billingEnabled
              ? t('ecomSet.regenModal.confirmWithPrice', { price: formatFen(imageFen) })
              : t('ecomSet.regenModal.confirm')}
          </Button>
        </>
      }
    >
      <p className="pf-muted pf-ecom-modal-hint">{t('ecomSet.regenModal.hint')}</p>
      <label className="pf-tool-field">
        <span>{t('ecomSet.plan.scene')}</span>
        <textarea rows={4} value={scene} disabled={busy} onChange={(e) => setScene(e.target.value)} />
      </label>
      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
    </Modal>
  )
}

/** 点图放大预览 */
export function EcomSetPreviewModal({ item, onClose }: { item: EcomSetItem; onClose: () => void }) {
  const { t } = useI18n()
  const src = resolveToolMediaUrl(item.final_url)
  return (
    <Modal open onClose={onClose} size="xl" title={item.slot_label} className="pf-ecom-preview">
      <img src={src} alt={item.slot_label} />
      <a className="pf-tool-download" href={src} download target="_blank" rel="noreferrer">
        {t('ecomSet.result.downloadImage')}
      </a>
    </Modal>
  )
}
