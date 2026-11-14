import { useEffect, useId, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useAnchoredPopover } from '../../hooks/useAnchoredPopover'
import { useGenerationText } from '../../lib/generationMedia'

function dayKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function parseDay(value: string) { return new Date(`${value}T12:00:00`) }
function shiftMonth(value: Date, amount: number) { return new Date(value.getFullYear(), value.getMonth() + amount, 1) }

type Props = { from: string; to: string; onChange: (from: string, to: string) => void }

export default function GenerationDateRange({ from, to, onChange }: Props) {
  const text = useGenerationText()
  const { open, setOpen, triggerRef, panelRef, position } = useAnchoredPopover(610, 510)
  const [draftFrom, setDraftFrom] = useState(from)
  const [draftTo, setDraftTo] = useState(to)
  const [hovered, setHovered] = useState('')
  const [month, setMonth] = useState(() => shiftMonth(from ? parseDay(from) : new Date(), 0))
  const id = useId()
  const today = dayKey(new Date())
  useEffect(() => {
    if (!open) return
    const frame = requestAnimationFrame(() => {
      const day = panelRef.current?.querySelector<HTMLButtonElement>(`[data-calendar-day="${from || today}"]`)
      day?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [open, from, today, panelRef])
  const presets = [
    { label: text('全部时间', 'All time'), start: '', end: '' },
    ...[7, 30].map(days => {
      const start = new Date(); start.setDate(start.getDate() - days + 1)
      return { label: text(`最近 ${days} 天`, `Last ${days} days`), start: dayKey(start), end: today }
    }),
    { label: text('本月', 'This month'), start: dayKey(shiftMonth(new Date(), 0)), end: today },
  ]

  function pick(value: string) {
    if (!draftFrom || draftTo) { setDraftFrom(value); setDraftTo('') }
    else if (value < draftFrom) { setDraftTo(draftFrom); setDraftFrom(value) }
    else setDraftTo(value)
    setHovered('')
  }
  function dayKeyboard(event: KeyboardEvent<HTMLButtonElement>, value: string) {
    const offsets: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }
    if (!(event.key in offsets)) return
    event.preventDefault()
    const date = parseDay(value)
    date.setDate(date.getDate() + offsets[event.key])
    const next = dayKey(date)
    const target = panelRef.current?.querySelector<HTMLButtonElement>(`[data-calendar-day="${next}"]`)
    if (target && target.getClientRects().length) target.focus()
    else {
      setMonth(shiftMonth(date, 0))
      requestAnimationFrame(() => panelRef.current?.querySelector<HTMLButtonElement>(`[data-calendar-day="${next}"]`)?.focus())
    }
  }
  function calendar(offset: number) {
    const current = shiftMonth(month, offset)
    const start = (current.getDay() + 6) % 7
    const days = new Date(current.getFullYear(), current.getMonth() + 1, 0).getDate()
    const end = draftTo || hovered || draftFrom
    const rangeStart = draftFrom < end ? draftFrom : end
    const rangeEnd = draftFrom < end ? end : draftFrom
    return <div className={`gen-calendar-month ${offset ? 'is-second' : ''}`}>
      <div className="gen-calendar-month-label">{current.toLocaleDateString(text('zh-CN', 'en-US'), { year: 'numeric', month: 'long' })}</div>
      <div className="gen-calendar-weekdays">{text('一 二 三 四 五 六 日', 'M T W T F S S').split(' ').map((day, index) => <span key={index}>{day}</span>)}</div>
      <div className="gen-calendar-grid" onPointerLeave={() => setHovered('')}>
        {Array.from({ length: start }, (_, index) => <span key={`blank-${index}`} />)}
        {Array.from({ length: days }, (_, index) => {
          const value = dayKey(new Date(current.getFullYear(), current.getMonth(), index + 1))
          const edge = value === draftFrom || value === draftTo
          const inside = draftFrom && value >= rangeStart && value <= rangeEnd
          return <button type="button" key={value} data-calendar-day={value}
            aria-label={value} aria-pressed={Boolean(edge)} title={value}
            className={`${inside ? 'is-in-range' : ''} ${edge ? 'is-range-edge' : ''} ${value === today ? 'is-today' : ''}`}
            onClick={() => pick(value)} onPointerEnter={() => { if (draftFrom && !draftTo) setHovered(value) }}
            onKeyDown={event => dayKeyboard(event, value)}>{index + 1}</button>
        })}
      </div>
    </div>
  }
  const display = from ? `${from.replaceAll('-', '.')} — ${(to || from).replaceAll('-', '.')}` : text('全部时间', 'All time')

  return <div className={`gen-date-control ${from ? 'has-value' : ''}`}>
    <button type="button" className={`gen-date-trigger ${open ? 'is-open' : ''}`} ref={triggerRef}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} aria-label={text('选择日期范围', 'Choose date range')}
      onClick={() => { setDraftFrom(from); setDraftTo(to); setHovered(''); setMonth(shiftMonth(from ? parseDay(from) : new Date(), 0)); setOpen(previous => !previous) }}>
      <CalendarDays size={17} aria-hidden /><span>{display}</span><ChevronDown size={14} aria-hidden />
    </button>
    {from && <button type="button" className="gen-date-clear" aria-label={text('清除日期筛选', 'Clear date filter')} onClick={() => onChange('', '')}><X size={13} /></button>}
    {open && createPortal(<div ref={panelRef} style={position} className="gen-floating gen-calendar-popup" role="dialog" id={id} aria-label={text('选择时间范围', 'Select date range')}>
      <header className="gen-calendar-header"><span><CalendarDays size={17} />{text('选择时间范围', 'Select date range')}</span><button type="button" className="gen-plain-icon" aria-label={text('关闭日期选择', 'Close date picker')} onClick={() => { setOpen(false); triggerRef.current?.focus() }}><X size={16} /></button></header>
      <div className="gen-calendar-presets">{presets.map(preset => <button type="button" key={preset.label}
        className={draftFrom === preset.start && draftTo === preset.end ? 'is-active' : ''}
        onClick={() => { setDraftFrom(preset.start); setDraftTo(preset.end); setHovered(''); setMonth(shiftMonth(preset.start ? parseDay(preset.start) : new Date(), 0)) }}>{preset.label}{draftFrom === preset.start && draftTo === preset.end && <Check size={12} />}</button>)}</div>
      <div className="gen-calendars"><button type="button" className="gen-calendar-prev gen-plain-icon" aria-label={text('上个月', 'Previous month')} onClick={() => setMonth(value => shiftMonth(value, -1))}><ChevronLeft size={18} /></button>
        {calendar(0)}{calendar(1)}
        <button type="button" className="gen-calendar-next gen-plain-icon" aria-label={text('下个月', 'Next month')} onClick={() => setMonth(value => shiftMonth(value, 1))}><ChevronRight size={18} /></button>
      </div>
      <footer className="gen-calendar-footer"><div><span>{draftFrom || text('开始日期', 'Start date')}</span><ArrowRight size={14} /><span>{draftTo || (draftFrom ? text('选择结束日期', 'Choose end date') : text('结束日期', 'End date'))}</span></div>
        <button type="button" className="gen-primary" onClick={() => { onChange(draftFrom, draftFrom ? draftTo || draftFrom : ''); setOpen(false); triggerRef.current?.focus() }}>{text('应用筛选', 'Apply')}</button>
      </footer>
    </div>, document.body)}
  </div>
}
