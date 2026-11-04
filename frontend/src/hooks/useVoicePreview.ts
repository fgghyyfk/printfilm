import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'

/** 两处音色选择共用播放器，迟到试听响应不能在切声或离页后自行播放。 */
export function useVoicePreview(onError: (message: string) => void) {
  const [previewBusy, setPreviewBusy] = useState<string | null>(null)
  const [playingId, setPlayingId] = useState<string | null>(null)
  const [isFallback, setIsFallback] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const serial = useRef(0)
  const keyRef = useRef('')
  const errorRef = useRef(onError)
  errorRef.current = onError
  const stopPreview = useCallback(() => {
    serial.current += 1
    audioRef.current?.pause()
    audioRef.current = null
    keyRef.current = ''
    setPlayingId(null)
    setPreviewBusy(null)
    setIsFallback(false)
  }, [])
  useEffect(() => () => stopPreview(), [stopPreview])
  const previewVoice = useCallback(async (voiceId: string, scene: 'kepu' | 'daihuo' | 'drama' = 'kepu') => {
    const key = `${scene}:${voiceId}`
    if (keyRef.current === key && audioRef.current && !audioRef.current.paused) { stopPreview(); return }
    stopPreview()
    const request = serial.current
    setPreviewBusy(voiceId)
    try {
      const result = await api.previewVoice(voiceId, scene)
      if (serial.current !== request) return
      const audio = new Audio(api.assetUrl(result.url))
      audioRef.current = audio
      keyRef.current = key
      audio.onended = () => { if (serial.current === request) setPlayingId(null) }
      audio.onerror = () => { if (serial.current === request) { setPlayingId(null); errorRef.current('试听播放失败') } }
      setPlayingId(voiceId)
      setIsFallback(Boolean(result.is_fallback))
      await audio.play()
    } catch (err) {
      if (serial.current === request) { setPlayingId(null); errorRef.current(err instanceof Error ? err.message : '试听失败') }
    } finally {
      if (serial.current === request) setPreviewBusy(null)
    }
  }, [stopPreview])
  return { previewBusy, playingId, isFallback, previewVoice, stopPreview }
}
