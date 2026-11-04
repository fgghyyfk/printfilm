import { useEffect, useState } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { listEcomProducts, type EcomProduct } from '../../api/ecomProducts'
import type { EcomStyledCategory } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { useGenerationText } from '../../lib/generationMedia'
import ProductForm from './ProductForm'

type Props = {
  categories: EcomStyledCategory[]
  product: EcomProduct | null
  colorIndex: number
  onPick: (product: EcomProduct, colorIndex: number) => void
}

/** 第①步：从「我的商品」选一款并选颜色；没有就新建 */
export default function ProductStep({ categories, product, colorIndex, onPick }: Props) {
  const text = useGenerationText()
  /* 商品列表三态 + 新建表单开关 */
  const [items, setItems] = useState<EcomProduct[] | null>(null)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    void listEcomProducts(1, 50, controller.signal)
      .then((r) => { setItems(r.items); if (!r.items.length) setCreating(true) })
      .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')) })
    return () => controller.abort()
  }, [text])

  const catName = (id: string) => categories.find((c) => c.id === id)?.name || id

  if (creating) {
    return <ProductForm categories={categories} onCancel={() => setCreating(false)}
      onSaved={(saved) => { setItems((prev) => [saved, ...(prev || [])]); setCreating(false); onPick(saved, 0) }} />
  }
  if (error) return <p className="pf-tool-error">{error}</p>
  if (!items) return <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /> {text('加载商品…', 'Loading…')}</p>

  return <div className="pf-es-step">
    <div className="pf-es-grid">
      <button type="button" className="pf-es-card pf-es-card-new" onClick={() => setCreating(true)}>
        <Plus size={26} /><strong>{text('新建商品', 'New product')}</strong><span className="pf-muted">{text('上传平铺图，录一次就能反复用', 'Upload once, reuse anytime')}</span>
      </button>
      {items.map((p) => <button key={p.id} type="button" className={`pf-es-card${product?.id === p.id ? ' active' : ''}`} onClick={() => onPick(p, 0)}>
        <img src={resolveToolMediaUrl(p.colors[0]?.image_url)} alt="" />
        <strong>{p.name}</strong>
        <span className="pf-muted">{[catName(p.category), p.material_label, p.size && (p.category === 'scarf' ? `${p.size}cm` : p.size)].filter(Boolean).join(' · ')}</span>
        <span className="pf-muted">{text(`${p.colors.length} 个颜色`, `${p.colors.length} colors`)}{p.fabric_image_url ? text(' · 有面料图', ' · fabric') : ''}</span>
      </button>)}
    </div>
    {product ? <div className="pf-tool-field">
      <span>{text('选择颜色款', 'Colorway')}</span>
      <div className="pf-es-swatches">
        {product.colors.map((c, i) => <button key={c.image_url} type="button" className={`pf-es-swatch${colorIndex === i ? ' active' : ''}`} onClick={() => onPick(product, i)}>
          <img src={resolveToolMediaUrl(c.image_url)} alt="" /><span>{c.name}</span>
        </button>)}
      </div>
    </div> : null}
  </div>
}
