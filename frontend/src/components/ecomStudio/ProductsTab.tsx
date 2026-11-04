import { useEffect, useState } from 'react'
import { ImagePlus, Loader2, Plus, Trash2, X } from 'lucide-react'
import {
  addEcomProductColor, deleteEcomProduct, listEcomProducts, removeEcomProductColor, setEcomProductFabric, type EcomProduct,
} from '../../api/ecomProducts'
import type { EcomStyledCategory } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { ECOM_UPLOAD_ACCEPT } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import { dialog } from '../../lib/dialog'
import ProductForm from './ProductForm'

type Props = { categories: EcomStyledCategory[]; onUse: (product: EcomProduct) => void }

/** 「我的商品」管理：新建、加/删颜色款、面料实拍、删除商品 */
export default function ProductsTab({ categories, onUse }: Props) {
  const text = useGenerationText()
  /* 列表三态与操作状态 */
  const [items, setItems] = useState<EcomProduct[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    void listEcomProducts(1, 50, controller.signal).then((r) => setItems(r.items))
      .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')) })
    return () => controller.abort()
  }, [text])

  // 统一执行一次商品操作并替换列表里的那一项
  async function act(id: number, run: () => Promise<EcomProduct | null>) {
    setBusy(id); setError('')
    try {
      const next = await run()
      setItems((prev) => (prev || []).flatMap((p) => (p.id !== id ? [p] : next ? [next] : [])))
    } catch (err) { setError(err instanceof Error ? err.message : text('操作失败', 'Failed')) }
    finally { setBusy(null) }
  }

  const catName = (id: string) => categories.find((c) => c.id === id)?.name || id
  if (creating) return <ProductForm categories={categories} onCancel={() => setCreating(false)}
    onSaved={(p) => { setItems((prev) => [p, ...(prev || [])]); setCreating(false) }} />
  if (!items) return error ? <p className="pf-tool-error">{error}</p> : <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /></p>

  return <div className="pf-es-step">
    <div className="pf-es-actions"><button type="button" className="pf-es-link" onClick={() => setCreating(true)}><Plus size={14} />{text('新建商品', 'New product')}</button></div>
    {!items.length ? <p className="pf-muted">{text('还没有商品。新建一款，以后出图直接选。', 'No products yet.')}</p> : null}
    {error ? <p className="pf-tool-error">{error}</p> : null}
    {items.map((p) => <div key={p.id} className="pf-tool-panel pf-es-product">
      <div className="pf-es-product-head">
        <div><strong>{p.name}</strong><span className="pf-muted"> · {[catName(p.category), p.material_label, p.size].filter(Boolean).join(' · ')}</span></div>
        <div className="pf-es-actions">
          <button type="button" className="pf-es-link" onClick={() => onUse(p)}>{text('用它出图', 'Create images')}</button>
          <button type="button" className="pf-es-link" disabled={busy === p.id} onClick={() => void (async () => {
            if (!(await dialog.confirm(text(`删除商品「${p.name}」？已生成的图不受影响。`, `Delete ${p.name}?`)))) return
            await act(p.id, async () => { await deleteEcomProduct(p.id); return null })
          })()}><Trash2 size={14} />{text('删除', 'Delete')}</button>
        </div>
      </div>
      {p.notes ? <p className="pf-muted">{p.notes}</p> : null}
      <div className="pf-es-colors">
        {p.colors.map((c, i) => <div key={c.image_url} className="pf-es-color">
          <img src={resolveToolMediaUrl(c.image_url)} alt="" /><span>{c.name}</span>
          {p.colors.length > 1 ? <button type="button" className="pf-es-x" disabled={busy === p.id} aria-label={text('删除颜色', 'Remove color')}
            onClick={() => void act(p.id, () => removeEcomProductColor(p.id, i))}><X size={12} /></button> : null}
        </div>)}
        <label className="pf-es-add"><Plus size={16} /><span>{text('加颜色', 'Add color')}</span>
          <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy === p.id} onChange={(e) => {
            const f = e.target.files?.[0]; e.target.value = ''
            if (!f) return
            void (async () => {
              const name = (await dialog.prompt({ title: text('颜色名', 'Color name'), placeholder: text('如：湖绿', 'e.g. Teal') })) ?? ''
              await act(p.id, () => addEcomProductColor(p.id, name || '', f))
            })()
          }} />
        </label>
        {p.fabric_image_url ? <div className="pf-es-color"><img src={resolveToolMediaUrl(p.fabric_image_url)} alt="" /><span>{text('面料', 'Fabric')}</span></div> : null}
        <label className="pf-es-add"><ImagePlus size={16} /><span>{p.fabric_image_url ? text('换面料图', 'Replace fabric') : text('加面料图', 'Add fabric')}</span>
          <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy === p.id} onChange={(e) => {
            const f = e.target.files?.[0]; e.target.value = ''
            if (f) void act(p.id, () => setEcomProductFabric(p.id, f))
          }} />
        </label>
      </div>
    </div>)}
  </div>
}
