import { getActiveLocale } from './detect'
import { messages } from './messages'
import { interpolate, lookupMessage, type TVars } from './lookup'

/** Non-React UI utilities resolve the current locale at call time. */
export function translate(path: string, vars?: TVars): string {
  const raw = lookupMessage(messages[getActiveLocale()], path)
  return raw === undefined ? path : interpolate(raw, vars)
}
