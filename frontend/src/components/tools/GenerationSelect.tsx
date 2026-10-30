import { useId, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'
import { useAnchoredPopover } from '../../hooks/useAnchoredPopover'

type Option = { value: string; label: string; description?: string; icon?: ReactNode }
type Props = {
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
  icon?: ReactNode
  className?: string
  disabled?: boolean
  compact?: boolean
}

export default function GenerationSelect({ label, value, options, onChange, icon, className = '', disabled, compact }: Props) {
  const { open, setOpen, triggerRef, panelRef, position } = useAnchoredPopover(260, Math.min(380, options.length * 72 + 52))
  const id = useId()
  const selectedIndex = Math.max(0, options.findIndex(option => option.value === value))
  const selected = options[selectedIndex]
  const [active, setActive] = useState(selectedIndex)
  const [typed, setTyped] = useState({ text: '', at: 0 })

  function choose(index: number) {
    if (!options[index]) return
    onChange(options[index].value)
    setOpen(false)
    triggerRef.current?.focus()
  }
  function keyboard(event: KeyboardEvent<HTMLButtonElement>) {
    const index = open ? active : selectedIndex
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
      setActive(next)
      setOpen(true)
      requestAnimationFrame(() => panelRef.current?.querySelector(`#${CSS.escape(`${id}-${next}`)}`)?.scrollIntoView({ block: 'nearest' }))
    } else if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault()
      choose(active)
    } else if (event.key === 'Tab') {
      setOpen(false)
    } else if (event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey) {
      const query = Date.now() - typed.at < 700 ? typed.text + event.key : event.key
      setTyped({ text: query, at: Date.now() })
      const match = options.findIndex(option => option.label.toLowerCase().startsWith(query.toLowerCase()))
      if (match >= 0) { setActive(match); setOpen(true) }
    }
  }

  return <div className={`gen-select ${compact ? 'is-compact' : ''} ${className}`}>
    <button type="button" role="combobox" ref={triggerRef} className={`gen-select-trigger ${open ? 'is-open' : ''}`}
      aria-label={label} aria-expanded={open} aria-haspopup="listbox" aria-controls={open ? id : undefined}
      aria-activedescendant={open ? `${id}-${active}` : undefined} disabled={disabled} onKeyDown={keyboard}
      onClick={() => { setActive(selectedIndex); setOpen(previous => !previous) }}>
      {(selected?.icon || icon) && <span className="gen-select-icon" aria-hidden>{selected?.icon || icon}</span>}
      <span className="gen-select-value">{!compact && <small>{label}</small>}<strong>{selected?.label || value}</strong></span>
      <ChevronDown size={15} className="gen-select-chevron" aria-hidden />
    </button>
    {open && createPortal(<div ref={panelRef} style={position} className="gen-floating gen-select-popup">
      <div className="gen-popup-label">{label}</div>
      <div role="listbox" id={id} aria-label={label}>
        {options.map((option, index) => <div key={option.value} id={`${id}-${index}`} role="option" aria-selected={option.value === value}
          className={`gen-select-option ${option.value === value ? 'is-selected' : ''} ${active === index ? 'is-highlighted' : ''}`}
          onPointerMove={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(index)}>
          {(option.icon || icon) && <span className="gen-option-icon" aria-hidden>{option.icon || icon}</span>}
          <span><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
          {option.value === value && <Check size={16} className="gen-option-check" aria-hidden />}
        </div>)}
      </div>
    </div>, document.body)}
  </div>
}
