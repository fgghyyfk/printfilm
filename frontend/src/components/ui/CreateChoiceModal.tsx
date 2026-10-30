import { useNavigate } from 'react-router-dom'
import { Clapperboard, PanelsTopLeft, Video, Mic } from 'lucide-react'
import { useI18n } from '../../i18n'
import { SHORT_VIDEO_CREATE_PATH } from '../../lib/shortVideoRoutes'
import Modal from './Modal'

type Props = {
  open: boolean
  onClose: () => void
}

/** 开始创作：选择漫剧、自由画布或短视频。 */
export default function CreateChoiceModal({ open, onClose }: Props) {
  const nav = useNavigate()
  const { t } = useI18n()

  // 跳转到目标产品并关闭弹层
  function go(path: string) {
    onClose()
    nav(path)
  }

  return (
    <Modal open={open} onClose={onClose} title={t('create.title')} size="md">
      <p className="pf-muted" style={{ marginTop: 0 }}>
        {t('create.hint')}
      </p>
      <div className="pf-create-choice">
        <button type="button" className="pf-create-choice-card" onClick={() => go('/drama')}>
          <span className="pf-ws-product-icon" aria-hidden>
            <Clapperboard size={24} strokeWidth={1.6} />
          </span>
          <strong>{t('create.drama')}</strong>
          <span className="pf-muted">{t('create.dramaHint')}</span>
        </button>
        <button type="button" className="pf-create-choice-card" onClick={() => go('/canvas')}>
          <span className="pf-ws-product-icon" aria-hidden><PanelsTopLeft size={24} strokeWidth={1.6} /></span>
          <strong>{t('create.canvas')}</strong>
          <span className="pf-muted">{t('create.canvasHint')}</span>
        </button>
        <button type="button" className="pf-create-choice-card" onClick={() => go(`${SHORT_VIDEO_CREATE_PATH}?mode=avatar`)}>
          <span className="pf-ws-product-icon" aria-hidden>
            <Mic size={24} strokeWidth={1.6} />
          </span>
          <strong>{t('nav.kaikou')}</strong>
          <span className="pf-muted">{t('create.avatarHint')}</span>
        </button>
        <button type="button" className="pf-create-choice-card" onClick={() => go(SHORT_VIDEO_CREATE_PATH)}>
          <span className="pf-ws-product-icon" aria-hidden>
            <Video size={24} strokeWidth={1.6} />
          </span>
          <strong>{t('create.kepu')}</strong>
          <span className="pf-muted">{t('create.kepuHint')}</span>
        </button>
      </div>
    </Modal>
  )
}
