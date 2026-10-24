import { useCallback } from 'react'
import { AudioLines, Image, Video } from 'lucide-react'
import { useI18n } from '../i18n'

export type MediaKind = 'video' | 'image' | 'audio'
export const mediaKinds: MediaKind[] = ['video', 'image', 'audio']
export const mediaIcons = { video: Video, image: Image, audio: AudioLines }

export function useGenerationText() {
  const { locale } = useI18n()
  return useCallback((zh: string, en: string) => locale === 'en' ? en : zh, [locale])
}
