import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { EcomLanguage, EcomSetCopyPatch, EcomSetItem } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { useI18n } from '../../i18n'
import { ecomSlotLabel, formatFen, titleLimitOf } from '../../lib/ecomSet'
import { dialog } from '../../lib/dialog'
import { useGenerationText } from '../../lib/generationMedia'
import ItemLayoutEditor from './ItemLayoutEditor'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import Modal from '../ui/Modal'
import EcomSetCopyFields from './EcomSetCopyFields'

type CopyProps = {
  language?: EcomLanguage
  item: EcomSetItem
  busy: boolean
  error: string
  onClose: () => void
  onSave: (copy: EcomSetCopyPatch) => void
}

/** 改文案弹层：编辑后 PUT copy，同步重新排版（免费）；父组件以 item 作 key 重置 */
export function EcomSetCopyModal({ item, busy, error, onClose, onSave, language }: CopyProps) {
  const { t } = useI18n()
  const [copy, setCopy] = useState<EcomSetCopyPatch>({
    title: item.title,
    subtitle: item.subtitle,
    bullets: item.bullets,
    en: item.en || '',
  })

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={t('ecomSet.copyModal.title', { label: ecomSlotLabel(t, item) })}
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
        <EcomSetCopyFields language={language} value={copy} onChange={setCopy} disabled={busy} titleMax={titleLimitOf(item.slot)} />
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
  /** scene 未改动时传 undefined；layout 未改动时传 undefined */
  onConfirm: (scene: string | undefined, layout?: { shot?: string; elements?: string[] }) => void
  /** 镜头库与本套元素池 */
  shots?: { key: string; name: string }[]
  pool?: { key: string; name: string; image_url?: string }[]
  /** 新建一个元素并加入本套元素池，返回新元素 */
  onAddElement?: (name: string) => Promise<{ key: string; name: string } | null>
}

/** 重新生成弹层：可改画面描述，按钮标价，确认即收一张生图费 */
export function EcomSetRegenModal({ item, imageFen, billingEnabled, busy, error, onClose, onConfirm, shots = [], pool = [],
  onAddElement }: RegenProps) {
  const { t } = useI18n()
  const text = useGenerationText()
  const [scene, setScene] = useState(item.scene)
  /* 本张镜头与元素（可改） */
  const [layout, setLayout] = useState({ shot: item.shot || '', elements: item.elements || [] })
  const [adding, setAdding] = useState(false)
  const trimmed = scene.trim()
  const layoutChanged = layout.shot !== (item.shot || '') ||
    layout.elements.map((e) => e.key).join() !== (item.elements || []).map((e) => e.key).join()

  // 一句话新建元素并选上
  async function addElement() {
    if (!onAddElement) return
    const name = ((await dialog.prompt({ title: text('添加画面元素', 'Add element'), placeholder: text('如：白色山茶花、黄铜托盘', 'e.g. white camellia') })) || '').trim()
    if (!name) return
    setAdding(true)
    try {
      const el = await onAddElement(name)
      if (el && layout.elements.length < 4) setLayout({ ...layout, elements: [...layout.elements, el] })
    } finally { setAdding(false) }
  }

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={t('ecomSet.regenModal.title', { label: ecomSlotLabel(t, item) })}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            {t('ecomSet.regenModal.cancel')}
          </Button>
          <Button
            variant="lime"
            disabled={busy || (!trimmed && item.billable !== false && !item.slot.startsWith('goods_info'))}
            onClick={() => onConfirm(trimmed === item.scene.trim() ? undefined : trimmed,
              layoutChanged ? { shot: layout.shot || undefined, elements: layout.elements.map((e) => e.key) } : undefined)}
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
      {item.shot || item.elements?.length || pool.length ? <>
        <ItemLayoutEditor shot={layout.shot} elements={layout.elements} shots={shots} pool={pool} disabled={busy}
          onChange={setLayout} />
        {onAddElement ? <button type="button" className="pf-es-link" disabled={busy || adding} onClick={() => void addElement()}>
          {adding ? <Loader2 size={14} className="pf-tool-spin" aria-hidden /> : null}{text('+ 新建元素', '+ New element')}
        </button> : null}
      </> : null}
      {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
    </Modal>
  )
}

/** 点图放大预览 */
export function EcomSetPreviewModal({ item, onClose }: { item: EcomSetItem; onClose: () => void }) {
  const { t } = useI18n()
  const src = resolveToolMediaUrl(item.final_url)
  return (
    <Modal open onClose={onClose} size="xl" title={ecomSlotLabel(t, item)} className="pf-ecom-preview">
      <img src={src} alt={ecomSlotLabel(t, item)} />
      <a className="pf-tool-download" href={src} download target="_blank" rel="noreferrer">
        {t('ecomSet.result.downloadImage')}
      </a>
    </Modal>
  )
}
