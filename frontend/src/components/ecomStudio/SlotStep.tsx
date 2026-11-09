import { Check, Droplet, ImageIcon, Layers, Shirt, UserRound } from 'lucide-react'
import type { EcomStyledCategory } from '../../api/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'

type Props = {
  category: EcomStyledCategory
  frames: { id: string; name: string }[]
  frame: string
  slots: string[]
  onFrame: (id: string) => void
  onSlots: (keys: string[]) => void
}

/** 有示例图的槽位（public/ecom-examples/{key}.jpg） */
const EXAMPLES = new Set(['scarf_cover', 'scarf_hold', 'scarf_texture', 'scarf_knot', 'scarf_size', 'scarf_crop',
  'scarf_closeup', 'scarf_long', 'scarf_back', 'scarf_street', 'scarf_flat'])

/** 第③步：选画幅 + 勾选要生成的图（整套快捷选择，或只挑一两张） */
export default function SlotStep({ category, frames, frame, slots, onFrame, onSlots }: Props) {
  const text = useGenerationText()

  // 勾选 / 取消一张，保持注册表里的顺序
  function toggle(key: string) {
    const next = slots.includes(key) ? slots.filter((k) => k !== key) : [...slots, key]
    onSlots(category.slots.map((s) => s.key).filter((k) => next.includes(k)))
  }

  const refLabel = {
    model: text('模特上身', 'On model'), product: text('商品特写', 'Product'), flat: text('免费', 'Free'),
    composite: text('原图合成', 'Composite'), texture: text('质地特写', 'Texture'),
  }
  const RefIcon = { model: UserRound, product: Shirt, flat: ImageIcon, composite: Layers, texture: Droplet }

  return <div className="pf-es-step">
    <div className="pf-tool-field"><span>{text('画幅', 'Format')}</span>
      <div className="pf-tool-chips">{frames.map((f) => <button key={f.id} type="button" className={frame === f.id ? 'active' : ''} onClick={() => onFrame(f.id)}>{f.name}</button>)}</div>
    </div>
    <div className="pf-tool-field"><span>{text('快速选择', 'Presets')}</span>
      <div className="pf-tool-chips">
        {category.counts.map((n) => <button key={n} type="button" className={slots.join() === (category.compositions[String(n)] || []).join() ? 'active' : ''}
          onClick={() => onSlots(category.compositions[String(n)] || [])}>{text(`整套详情页 ${n} 张`, `Full set · ${n}`)}</button>)}
        <button type="button" onClick={() => onSlots([])}>{text('清空，自己挑', 'Clear')}</button>
      </div>
    </div>
    <div className="pf-es-slots">
      {category.slots.map((s) => {
        const on = slots.includes(s.key)
        const Icon = RefIcon[s.ref]
        return <button key={s.key} type="button" className={`pf-es-slot${on ? ' active' : ''}`} aria-pressed={on} onClick={() => toggle(s.key)}>
          {EXAMPLES.has(s.key) ? <img src={`/ecom-examples/${s.key}.jpg`} alt="" loading="lazy" /> : <span className="pf-es-slot-ph"><Icon size={28} /></span>}
          <strong>{s.label}</strong>
          <span className={`pf-es-ref pf-es-ref-${s.ref}`}>{refLabel[s.ref]}</span>
          {on ? <span className="pf-es-tick"><Check size={14} /></span> : null}
        </button>
      })}
    </div>
    <p className="pf-muted">{category.uses_model === false
      ? text(`已选 ${slots.length} 张。「原图合成」由 AI 按所选场景搭景，商品用你上传的透明底原图贴回，包装文字不会被改；「免费」的直接排版，不调用 AI。`, `${slots.length} selected.`)
      : text(`已选 ${slots.length} 张。只要一张上身图就只勾一张；「免费」的直接用你的商品图排版，不调用 AI。`, `${slots.length} selected.`)}</p>
  </div>
}
