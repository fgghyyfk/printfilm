import { useEffect, useState } from 'react'
import { getPublicCapabilities } from '../api/publicCapabilities'
import { characterVoiceGenerationEnabled } from '../lib/publicCapabilities'

let enabled = false
let inflight: Promise<void> | null = null
const listeners = new Set<(value: boolean) => void>()

function refresh(): Promise<void> {
  if (!inflight) {
    inflight = getPublicCapabilities().then(characterVoiceGenerationEnabled).catch(() => false).then(value => {
      enabled = value
      listeners.forEach(receive => receive(value))
    }).finally(() => { inflight = null })
  }
  return inflight
}

/** Existing bindings remain available; only new AI voice creation follows this capability. */
export function useCharacterVoiceGenerationEnabled(): boolean {
  const [allowed, setAllowed] = useState(enabled)
  useEffect(() => {
    listeners.add(setAllowed)
    void refresh()
    const update = () => { void refresh() }
    const timer = window.setInterval(update, 60_000)
    window.addEventListener('focus', update)
    return () => {
      listeners.delete(setAllowed)
      window.clearInterval(timer)
      window.removeEventListener('focus', update)
    }
  }, [])
  return allowed
}
