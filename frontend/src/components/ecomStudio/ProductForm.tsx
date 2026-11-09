import { useState } from 'react'
import { ImagePlus, Loader2, Plus, X } from 'lucide-react'
import { createEcomProduct, type EcomProduct } from '../../api/ecomProducts'
import type { EcomStyledCategory } from '../../api/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import { ECOM_UPLOAD_ACCEPT, validateProductFile } from '../../lib/ecomSet'
import Button from '../ui/Button'

type Props = {
  categories: EcomStyledCategory[]
  onSaved: (product: EcomProduct) => void
  onCancel: () => void
}

type ColorDraft = { name: string; file: File; preview: string; real?: { file: File; preview: string } | null }

/** 新建商品：款名、品类、尺寸、材质、颜色款（平铺图）、面料实拍、卖点——录一次，以后直接选 */
export default function ProductForm({ categories, onSaved, onCancel }: Props) {
  const text = useGenerationText()
  /* 表单字段 */
  const [name, setName] = useState('')
  const [category, setCategory] = useState(categories[0]?.id || 'scarf')
  const cat = categories.find((c) => c.id === category) || categories[0]
  const [size, setSize] = useState(cat?.default_size || '')
  const [material, setMaterial] = useState(cat?.default_material || '')
  const [notes, setNotes] = useState('')
  const [colors, setColors] = useState<ColorDraft[]>([])
  const [fabric, setFabric] = useState<{ file: File; preview: string } | null>(null)
  /* 提交状态 */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // 切品类时重置尺寸和材质为该品类默认
  function pickCategory(id: string) {
    const next = categories.find((c) => c.id === id)
    setCategory(id)
    setSize(next?.default_size || '')
    setMaterial(next?.default_material || '')
  }

  // 选择颜色款图片（可多选，颜色名默认空，用户再填）
  function addColorFiles(files: FileList | null) {
    const picked: ColorDraft[] = []
    for (const file of Array.from(files || [])) {
      if (validateProductFile(file)) { setError(text('图片仅支持 PNG / JPG / WebP，且不超过 10MB', 'PNG / JPG / WebP up to 10MB')); continue }
      picked.push({ name: '', file, preview: URL.createObjectURL(file) })
    }
    setColors((prev) => [...prev, ...picked].slice(0, 12))
  }

  // 保存商品
  async function submit() {
    if (!name.trim()) return setError(text('请填写款名', 'Enter a product name'))
    if (!colors.length) return setError(text('请至少上传一张商品图', 'Add at least one product image'))
    setBusy(true); setError('')
    try {
      const saved = await createEcomProduct({
        name: name.trim(), category, size, material, notes: notes.trim(),
        colors: colors.map((c, i) => ({ name: c.name.trim() || text(`颜色${i + 1}`, `Color ${i + 1}`), file: c.file, real: c.real?.file })),
        fabric: fabric?.file,
      })
      onSaved(saved)
    } catch (err) { setError(err instanceof Error ? err.message : text('保存失败', 'Save failed')) }
    finally { setBusy(false) }
  }

  return <div className="pf-tool-panel pf-es-product-form">
    <h3>{text('新建商品', 'New product')}</h3>
    <label className="pf-tool-field"><span>{text('款名', 'Name')}</span>
      <input className="pf-ecom-input" maxLength={40} value={name} disabled={busy} placeholder={text('如：太平有象', 'e.g. Peace & Elephant')} onChange={(e) => setName(e.target.value)} />
    </label>
    <div className="pf-tool-field"><span>{text('品类', 'Category')}</span>
      <div className="pf-tool-chips">{categories.map((c) => <button key={c.id} type="button" disabled={busy} className={category === c.id ? 'active' : ''} onClick={() => pickCategory(c.id)}>{c.name}</button>)}</div>
    </div>
    {cat?.sizes.length ? <div className="pf-tool-field"><span>{text('尺寸', 'Size')}</span>
      <div className="pf-tool-chips">{cat.sizes.map((s) => <button key={s.id} type="button" disabled={busy} className={size === s.id ? 'active' : ''} onClick={() => setSize(s.id)}>{s.name}</button>)}</div>
    </div> : <label className="pf-tool-field"><span>{text('规格（选填）', 'Spec (optional)')}</span>
      <input className="pf-ecom-input" maxLength={20} value={size} disabled={busy} placeholder={text('如：均码 / 45cm 链长 / 中号', 'e.g. One size / 45cm')} onChange={(e) => setSize(e.target.value)} />
    </label>}
    <div className="pf-tool-field"><span>{text(cat?.material_label || '材质', cat?.uses_model === false ? 'Packaging' : 'Material')}</span>
      <div className="pf-tool-chips">{cat?.materials.map((m) => <button key={m.id} type="button" disabled={busy} className={material === m.id ? 'active' : ''} onClick={() => setMaterial(m.id)}>{m.name}</button>)}</div>
    </div>
    <div className="pf-tool-field"><span>{text('① 设计图（每个颜色一张：平铺设计稿或平铺实拍）', '① Design image per colorway')}</span>
      <div className="pf-es-colors">
        {colors.map((c, i) => <div className="pf-es-color" key={c.preview}>
          <img src={c.preview} alt="" />
          <input className="pf-ecom-input" maxLength={20} value={c.name} disabled={busy} placeholder={text('颜色名，如米白', 'Color name')}
            onChange={(e) => setColors((prev) => prev.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <button type="button" className="pf-es-x" aria-label={text('移除', 'Remove')} disabled={busy} onClick={() => setColors((prev) => prev.filter((_, j) => j !== i))}><X size={12} /></button>
          <label className="pf-es-real">{c.real ? <img src={c.real.preview} alt="" /> : <ImagePlus size={14} />}<span>{c.real ? text('实物图 ✓', 'Photo ✓') : text('+ 实物图', '+ Photo')}</span>
            <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy} onChange={(e) => {
              const f = e.target.files?.[0]; e.target.value = ''
              if (f) setColors((prev) => prev.map((x, j) => (j === i ? { ...x, real: { file: f, preview: URL.createObjectURL(f) } } : x)))
            }} />
          </label>
        </div>)}
        <label className="pf-es-add"><Plus size={18} /><span>{text('添加设计图', 'Add design')}</span>
          <input type="file" hidden multiple accept={ECOM_UPLOAD_ACCEPT} disabled={busy} onChange={(e) => { addColorFiles(e.target.files); e.target.value = '' }} />
        </label>
      </div>
      {category === 'goods' ? <p className="pf-es-tip">{text('请上传透明底 PNG 抠图：商品会原样合成进场景，不会被 AI 重画；普通照片也能用，但商品可能被重绘', 'Upload a transparent-background PNG: the product is composited into the scene as-is, not redrawn by AI. Regular photos work too, but the product may be redrawn.')}</p> : null}
      <span className="pf-muted">{text('设计图越清晰、越正，生成后的图案和颜色越准。每个颜色还可以加一张「实物图」（真实拍摄的商品照片），让真实光线下的颜色和质感更准。', 'Clear designs give accurate patterns. Add a real photo per color for true-to-life color.')}</span>
    </div>
    <div className="pf-tool-field"><span>{text('② 材质实拍（强烈推荐）', '② Fabric close-up (recommended)')}</span>
      <div className="pf-es-colors">
        {fabric ? <div className="pf-es-color"><img src={fabric.preview} alt="" /><button type="button" className="pf-es-x" onClick={() => setFabric(null)}><X size={12} /></button></div>
          : <label className="pf-es-add"><ImagePlus size={18} /><span>{text('上传面料近拍', 'Upload fabric photo')}</span>
            <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) setFabric({ file: f, preview: URL.createObjectURL(f) }); e.target.value = '' }} />
          </label>}
      </div>
      <span className="pf-muted">{text('手机贴近面料拍一张（能看清织纹与光泽），AI 会按它还原质感，比如人字纹、斜纹、缎面。', 'A close-up helps AI match weave and sheen.')}</span>
    </div>
    <label className="pf-tool-field"><span>{text('③ 成分与卖点（每行一条）', '③ Composition & selling points')}</span>
      <textarea rows={4} maxLength={500} value={notes} disabled={busy} placeholder={text('如：\n面料 30%真丝 + 70%羊毛\n纯手工卷边\n36 套色双面印花\n只写真实信息，会用于图上文案和面料描述', 'Only true facts — used for copy.')} onChange={(e) => setNotes(e.target.value)} />
    </label>
    {error ? <p className="pf-tool-error" role="alert">{error}</p> : null}
    <div className="pf-es-actions">
      <Button variant="outline" disabled={busy} onClick={onCancel}>{text('取消', 'Cancel')}</Button>
      <Button variant="lime" disabled={busy} onClick={() => void submit()}>{busy ? <><Loader2 size={16} className="pf-tool-spin" />{text('保存中…', 'Saving…')}</> : text('保存商品', 'Save product')}</Button>
    </div>
  </div>
}
