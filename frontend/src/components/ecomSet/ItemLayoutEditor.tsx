import { useGenerationText } from '../../lib/generationMedia'

type Props = {
  /** 本张当前镜头与元素 */
  shot: string
  elements: { key: string; name: string }[]
  /** 可选镜头（只有合成图有）与本套元素池 */
  shots: { key: string; name: string }[]
  pool: { key: string; name: string; image_url?: string }[]
  disabled?: boolean
  onChange: (next: { shot: string; elements: { key: string; name: string }[] }) => void
}

const MAX_ELEMENTS = 4

/** 单张的镜头（构图）+ 画面元素编辑：方案卡片与重做弹层共用 */
export default function ItemLayoutEditor({ shot, elements, shots, pool, disabled, onChange }: Props) {
  const text = useGenerationText()
  const picked = new Set(elements.map((e) => e.key))

  // 勾选 / 取消一个元素（最多 4 个）
  function toggle(el: { key: string; name: string }) {
    if (picked.has(el.key)) onChange({ shot, elements: elements.filter((e) => e.key !== el.key) })
    else if (elements.length < MAX_ELEMENTS) onChange({ shot, elements: [...elements, { key: el.key, name: el.name }] })
  }

  if (!shot && !pool.length) return null
  return <div className="pf-ecom-layout">
    {shot ? <label className="pf-tool-field"><span className="pf-ecom-field-label">{text('镜头', 'Shot')}</span>
      <select className="pf-ecom-input" value={shot} disabled={disabled} onChange={(e) => onChange({ shot: e.target.value, elements })}>
        {shots.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
      </select>
    </label> : null}
    {pool.length ? <div className="pf-tool-field"><span className="pf-ecom-field-label">{text('画面元素', 'Elements')}
      <em>{elements.length}/{MAX_ELEMENTS}</em></span>
      <div className="pf-tool-chips">{pool.map((el) => <button key={el.key} type="button" disabled={disabled}
        className={picked.has(el.key) ? 'active' : ''} onClick={() => toggle(el)}>
        {el.image_url ? '📷 ' : ''}{el.name}</button>)}</div>
    </div> : null}
  </div>
}
