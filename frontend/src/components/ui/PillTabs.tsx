type Props = {
  items: string[]
  labels?: Record<string, string>
  value: string
  onChange: (v: string) => void
  ariaLabel?: string
  lime?: boolean
}

export default function PillTabs({ items, labels, value, onChange, ariaLabel, lime }: Props) {
  return (
    <div className="pf-pill-tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={value === item}
          className={['pf-pill', lime ? 'lime' : '', value === item ? 'active' : ''].filter(Boolean).join(' ')}
          onClick={() => onChange(item)}
        >
          {labels?.[item] || item}
        </button>
      ))}
    </div>
  )
}
