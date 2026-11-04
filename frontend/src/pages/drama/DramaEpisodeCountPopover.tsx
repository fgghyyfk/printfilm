import { useI18n } from '../../i18n'
/** 自定义集数弹层：预设 + 手填 1–999 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ChevronDown } from 'lucide-react'
import { EPISODE_COUNT_PRESETS } from '../../lib/dramaImageStyles'

const CUSTOM_MIN = 1
const CUSTOM_MAX = 999

type Props = {
  value: number
  onChange: (count: number) => void
  disabled?: boolean
}

// 是否为预设集数
function isPreset(count: number) {
  return (EPISODE_COUNT_PRESETS as readonly number[]).includes(count)
}

// 解析自定义集数
function parseCustom(raw: string): number | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const n = Number.parseInt(trimmed, 10)
  if (!Number.isFinite(n) || n < CUSTOM_MIN || n > CUSTOM_MAX) return null
  return n
}

// 渲染集数选择弹层
export function DramaEpisodeCountPopover({ value, onChange, disabled = false }: Props) {
  const { t: translateUi } = useI18n()
  /*
   * open 弹层开关
   * customInput 自定义输入
   * rootRef / panelRef 点击外部关闭
   */
  const rootRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [customInput, setCustomInput] = useState(isPreset(value) ? '' : String(value))

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      const t = e.target as Node | null
      if (rootRef.current && t && !rootRef.current.contains(t)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  // 切换开关
  function toggle() {
    if (disabled) return
    setOpen((cur) => {
      const next = !cur
      if (next && !isPreset(value)) setCustomInput(String(value))
      return next
    })
  }

  // 选预设
  function selectPreset(count: number) {
    onChange(count)
    setCustomInput('')
    setOpen(false)
  }

  // 应用自定义
  function applyCustom() {
    const parsed = parseCustom(customInput)
    if (parsed == null) return
    onChange(parsed)
    setOpen(false)
  }

  function onCustomKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      applyCustom()
    }
  }

  const usingCustom = !isPreset(value)

  return (
    <div ref={rootRef} className="drama-ep-count-popover">
      <button
        type="button"
        className={`drama-agent-opt-trigger${open ? ' is-active' : ''}`}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={toggle}
      >
        <span>{translateUi("workflow.episodes", { v1: value })}</span>
        <ChevronDown size={13} strokeWidth={2} className={open ? 'is-open' : ''} />
      </button>

      {open ? (
        <div className="drama-ep-count-panel" role="dialog" aria-label={translateUi("workflow.customEpisodeCount")}>
          <p className="drama-ep-count-title">{translateUi("workflow.customEpisodeCount")}</p>
          <div className="drama-ep-count-presets">
            {EPISODE_COUNT_PRESETS.map((count) => (
              <button
                key={count}
                type="button"
                className={value === count ? 'is-active' : ''}
                onClick={() => selectPreset(count)}
              >
                {translateUi("workflow.episodes", { v1: count })}</button>
            ))}
          </div>
          <div className="drama-ep-count-custom">
            <p>{translateUi("workflow.customEpisodeCount")}</p>
            <div className="drama-ep-count-custom-row">
              <input
                type="number"
                min={CUSTOM_MIN}
                max={CUSTOM_MAX}
                value={customInput}
                placeholder={`${CUSTOM_MIN}-${CUSTOM_MAX}`}
                onChange={(e) => setCustomInput(e.target.value)}
                onKeyDown={onCustomKeyDown}
                className={usingCustom ? 'is-custom' : ''}
              />
              <button type="button" className="drama-ep-count-confirm" onClick={applyCustom}>
                {translateUi("workflow.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
