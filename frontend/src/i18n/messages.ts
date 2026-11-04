import type { Locale } from './detect'
import { en } from './locales/en'
import { zh } from './locales/zh'

type MessageShape<T> = {
  [K in keyof T]: T[K] extends string ? string : T[K] extends object ? MessageShape<T[K]> : T[K]
}

export type Messages = MessageShape<typeof zh>

export const messages: Record<Locale, Messages> = {
  zh,
  en,
}
