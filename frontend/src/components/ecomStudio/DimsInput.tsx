import { useState } from 'react'
import type { EcomDims } from '../../api/ecomProducts'
import { parseDim } from '../../lib/ecomDims'
import { useGenerationText } from '../../lib/generationMedia'

type Props = {
  value: EcomDims
  onChange: (next: EcomDims) => void
  disabled?: boolean
}

/** 商品实际尺寸输入：高 / 宽 / 厚（厘米），出图时道具、手与商品按真实比例搭配 */
export default function DimsInput({ value, onChange, disabled }: Props) {
  const text = useGenerationText()
  /* 输入中的原始字符串（允许输入「4.」这类中间态） */
  const [draft, setDraft] = useState<Record<string, string>>({})
  const fields = [['h', text('高', 'H')], ['w', text('宽', 'W')], ['d', text('厚', 'D')]] as const

  // 改一项：保留输入原文，合法数值才写回
  function edit(key: 'h' | 'w' | 'd', raw: string) {
    setDraft((prev) => ({ ...prev, [key]: raw }))
    onChange({ ...value, [key]: parseDim(raw) })
  }

  return <div className="pf-es-dims">
    {fields.map(([key, label]) => <label key={key}>
      <span>{label}</span>
      <input className="pf-ecom-input" inputMode="decimal" maxLength={6} disabled={disabled} placeholder="—"
        value={draft[key] ?? (value[key] ? String(value[key]) : '')} onChange={(e) => edit(key, e.target.value)} />
      <em>cm</em>
    </label>)}
  </div>
}
