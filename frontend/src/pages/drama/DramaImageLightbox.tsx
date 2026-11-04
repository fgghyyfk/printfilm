import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 资产图片全屏放大预览（点击遮罩 / Esc 关闭） */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

type Props = {
  src: string
  alt?: string
  onClose: () => void
}

// 渲染图片放大层
export function DramaImageLightbox({ src, alt = translateUi("ecomSet.result.preview"), onClose }: Props) {
  const { t: translateUi } = useI18n()
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey, true)
    }
  }, [onClose])

  return createPortal(
    <div
      className="drama-lightbox-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={translateUi("workflow.imagePreview")}
      onClick={onClose}
    >
      <button type="button" className="drama-lightbox-close" aria-label={translateUi("workflow.close")} onClick={onClose}>
        ×
      </button>
      <img
        className="drama-lightbox-img"
        src={src}
        alt={alt}
        onClick={(e) => e.stopPropagation()}
      />
    </div>,
    document.body,
  )
}
