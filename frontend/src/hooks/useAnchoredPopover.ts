import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/** Position a portalled control without clipping it inside cards or the viewport. */
export function useAnchoredPopover(preferredWidth = 260, preferredHeight = 360) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: 0, top: 0, width: preferredWidth, maxHeight: preferredHeight })

  useLayoutEffect(() => {
    if (!open) return
    function place() {
      const anchor = triggerRef.current?.getBoundingClientRect()
      if (!anchor) return
      const gap = 8
      const padding = 12
      const width = Math.min(Math.max(anchor.width, preferredWidth), window.innerWidth - padding * 2)
      const below = window.innerHeight - anchor.bottom - padding - gap
      const above = anchor.top - padding - gap
      const desiredHeight = Math.min(preferredHeight, (panelRef.current?.scrollHeight || preferredHeight) + 2)
      const upwards = below < desiredHeight && above > below
      const height = Math.min(desiredHeight, Math.max(120, upwards ? above : below))
      setPosition({
        left: Math.min(Math.max(padding, anchor.left), window.innerWidth - width - padding),
        top: upwards ? Math.max(padding, anchor.top - height - gap) : anchor.bottom + gap,
        width,
        maxHeight: height,
      })
    }
    place()
    const observer = new ResizeObserver(place)
    if (panelRef.current) observer.observe(panelRef.current)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      observer.disconnect()
    }
  }, [open, preferredWidth, preferredHeight])

  useEffect(() => {
    if (!open) return
    function outside(event: PointerEvent) {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false)
    }
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    function focusOutside(event: FocusEvent) {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keyboard)
    document.addEventListener('focusin', focusOutside)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keyboard)
      document.removeEventListener('focusin', focusOutside)
    }
  }, [open])

  return { open, setOpen, triggerRef, panelRef, position }
}
