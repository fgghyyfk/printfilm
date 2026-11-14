/** 生成页输入 @ 时弹出的提及层：从已加入的参考资料中选择并插入提及。 */
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { AudioLines, ImagePlus, Search, Video } from 'lucide-react'
import { resolveToolMediaUrl } from '../../api/tools'
import type { GenerationReference, GenerationReferenceKind } from '../../lib/generationReferences'
import { useGenerationText } from '../../lib/generationMedia'
import type { MentionCaretRect } from '../../lib/promptMention'
import '../../styles/generation-references.css'

type Props = {
  open: boolean
  items: GenerationReference[]
  anchorRect: MentionCaretRect | null
  activeIndex: number
  onActiveIndexChange: (index: number) => void
  onSelect: (reference: GenerationReference) => void
  onClose: () => void
}

const KIND_ICON: Record<GenerationReferenceKind, typeof ImagePlus> = { image: ImagePlus, video: Video, audio: AudioLines }

/** 光标锚定的参考资料提及弹层，键盘与鼠标均可选择。 */
export default function PromptMentionPopover({ open, items, anchorRect, activeIndex, onActiveIndexChange, onSelect, onClose }: Props) {
  const text = useGenerationText()
  const panelRef = useRef<HTMLDivElement>(null)

  // 点击弹层外部关闭
  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      const target = e.target as Node | null
      if (panelRef.current && target && !panelRef.current.contains(target)) onClose()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open, onClose])

  if (!open || !anchorRect) return null

  const top = Math.min(anchorRect.bottom + 8, window.innerHeight - 360)
  const left = Math.min(Math.max(12, anchorRect.left), window.innerWidth - 320)

  return createPortal(
    <div ref={panelRef} className="gen-mention-pop" style={{ top, left }} role="listbox" aria-label={text('@ 引用参考资料', 'Mention a reference')}>
      <p className="gen-mention-pop-heading"><Search size={13} aria-hidden />{text('提及已加入的参考资料', 'Mention a reference you added')}</p>
      {items.length === 0 ? (
        <p className="gen-mention-pop-empty">{text('没有匹配的参考资料，请先上传或从资产库选择图片、视频或音频。', 'No matching references. Add an image, video or audio first.')}</p>
      ) : (
        <div className="gen-mention-pop-list">
          {items.map((reference, index) => {
            const Icon = KIND_ICON[reference.kind]
            const preview = resolveToolMediaUrl(reference.url)
            return (
              <button
                key={reference.id}
                type="button"
                className={`gen-mention-pop-item${index === activeIndex ? ' is-active' : ''}`}
                onMouseDown={e => e.preventDefault()}
                onMouseEnter={() => onActiveIndexChange(index)}
                onClick={() => onSelect(reference)}
              >
                <span className="gen-mention-pop-thumb">{preview ? <img src={preview} alt="" /> : <Icon size={15} aria-hidden />}</span>
                <span className="gen-mention-pop-meta">
                  <strong>{reference.name}</strong>
                  <em>{reference.kind === 'image' ? text('图片', 'Image') : reference.kind === 'video' ? text('视频', 'Video') : text('音频', 'Audio')}</em>
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>,
    document.body,
  )
}
