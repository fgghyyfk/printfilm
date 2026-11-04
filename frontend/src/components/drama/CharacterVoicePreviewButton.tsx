import { useI18n } from '../../i18n'
/** 角色音色试听按钮（卡片内联播放，不弹窗） */
import { useEffect, useRef, useState } from 'react'
import { Pause, Volume2 } from 'lucide-react'
import { resolveDramaMediaUrl } from '../../api/drama'

type Props = {
  url: string
  label?: string
  className?: string
  size?: 'sm' | 'md'
  variant?: 'button' | 'chip' | 'inline'
  onError?: (message: string) => void
}

/** 同一时刻只播一条试听，避免多卡片叠音 */
let sharedAudio: HTMLAudioElement | null = null
let sharedStop: (() => void) | null = null

// 点击播放已绑定音色的试听音频，再点暂停
export function CharacterVoicePreviewButton({
  url,
  label,
  className = '',
  size = 'sm',
  variant = 'button',
  onError,
}: Props) {
  const { t: translateUi } = useI18n()
  const [playing, setPlaying] = useState(false)
  const src = resolveDramaMediaUrl(url)
  const stopRef = useRef<() => void>(() => undefined)

  useEffect(() => {
    return () => {
      if (sharedStop === stopRef.current) {
        sharedAudio?.pause()
        sharedStop = null
      }
    }
  }, [])

  function stop() {
    sharedAudio?.pause()
    if (sharedAudio) sharedAudio.currentTime = 0
    setPlaying(false)
    if (sharedStop === stopRef.current) sharedStop = null
  }

  stopRef.current = stop

  function handlePreview() {
    if (!src) {
      onError?.(translateUi("workflow.thePreviewUrlIsInvalid"))
      return
    }
    if (playing) {
      stop()
      return
    }
    sharedStop?.()
    if (!sharedAudio) sharedAudio = new Audio()
    sharedAudio.src = src
    sharedAudio.onended = () => {
      setPlaying(false)
      if (sharedStop === stopRef.current) sharedStop = null
    }
    sharedStop = () => stop()
    setPlaying(true)
    void sharedAudio.play().catch(() => {
      setPlaying(false)
      onError?.(translateUi("workflow.couldNotPlayAudio"))
    })
  }

  if (!src) return null

  let btnClass = 'drama-voice-preview-btn'
  if (variant === 'button') {
    const sizeClass = size === 'md' ? 'pf-btn pf-btn-ghost' : 'pf-btn pf-btn-ghost pf-btn-sm'
    btnClass = `${sizeClass} drama-voice-preview-btn`
  } else if (variant === 'chip') {
    btnClass = 'fc-toolbar-chip'
  }
  if (playing) btnClass = `${btnClass} is-playing`
  if (className) btnClass = `${btnClass} ${className}`

  return (
    <button
      type="button"
      className={btnClass}
      onClick={(e) => {
        e.stopPropagation()
        handlePreview()
      }}
      title={label ? translateUi("workflow.voicePreview", { v1: String(label) }) : translateUi("workflow.previewVoice")}
    >
      {playing ? <Pause size={14} strokeWidth={1.8} aria-hidden /> : <Volume2 size={14} strokeWidth={1.8} aria-hidden />}
      {playing ? translateUi("workflow.stop") : translateUi("workflow.previewVoice_854b12")}
    </button>
  )
}
