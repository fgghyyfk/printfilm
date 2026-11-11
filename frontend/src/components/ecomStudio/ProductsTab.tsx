import { useEffect, useState } from 'react'
import { ImagePlus, Loader2, Plus, Scissors, Trash2, Undo2, X } from 'lucide-react'
import {
  addEcomProductColor, colorsToCutout, cutoutEcomProductColor, deleteEcomProduct, restoreEcomProductColor, listEcomProducts, removeEcomProductColor, setEcomProductColorReal, setEcomProductFabric, type EcomProduct,
} from '../../api/ecomProducts'
import type { EcomStyledCategory } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { ECOM_UPLOAD_ACCEPT } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import { dialog } from '../../lib/dialog'
import KnowledgePanel from './KnowledgePanel'
import ProductForm from './ProductForm'

type Props = {
  categories: EcomStyledCategory[]
  onUse: (product: EcomProduct) => void
  /** 读参考详情的单价（分/批）与是否计费 */
  planFen?: number
  billingEnabled?: boolean
}

/** 「我的商品」管理：新建、加/删颜色款、面料实拍、删除商品 */
export default function ProductsTab({ categories, onUse, planFen = 0, billingEnabled = false }: Props) {
  const text = useGenerationText()
  /* 列表三态与操作状态 */
  const [items, setItems] = useState<EcomProduct[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  /* 正在抠图的颜色款（key = 商品id-下标） */
  const [cutting, setCutting] = useState<Record<string, boolean>>({})

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

  // 逐张抠图（本地模型每张 10~30 秒，按顺序跑，抠好一张换一张）
  async function runCutout(id: number, indexes: number[]) {
    for (const i of indexes) {
      const key = `${id}-${i}`
      setCutting((prev) => ({ ...prev, [key]: true }))
      try {
        const next = await cutoutEcomProductColor(id, i)
        setItems((prev) => (prev || []).map((p) => (p.id === id ? next : p)))
      } catch (err) { setError(err instanceof Error ? err.message : text('抠图失败', 'Cutout failed')) }
      finally { setCutting((prev) => { const rest = { ...prev }; delete rest[key]; return rest }) }
    }
  }

  // 商品新增或加了颜色后：日化商品的实拍图自动抠成透明底
  function afterUpload(p: EcomProduct | null) {
    if (p) void runCutout(p.id, colorsToCutout(p))
    return p
  }

  const catName = (id: string) => categories.find((c) => c.id === id)?.name || id
  if (creating) return <ProductForm categories={categories} onCancel={() => setCreating(false)}
    onSaved={(p) => { setItems((prev) => [p, ...(prev || [])]); setCreating(false); afterUpload(p) }} />
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
        {p.colors.map((c, i) => <div key={`${i}-${c.photo_image_url || c.image_url}`} className="pf-es-color">
          <img className={c.transparent ? 'pf-es-cutimg' : undefined} src={resolveToolMediaUrl(c.image_url)} alt="" /><span>{c.name}</span>
          {cutting[`${p.id}-${i}`] ? <span className="pf-es-cutting"><Loader2 size={14} className="pf-tool-spin" />{text('抠图中…', 'Cutting…')}</span> : null}
          {p.category === 'goods' && p.can_cutout && !cutting[`${p.id}-${i}`] ? (c.photo_image_url
            ? <button type="button" className="pf-es-link" disabled={busy === p.id} title={text('不用抠图，换回上传的原图', 'Use the original photo')}
              onClick={() => void act(p.id, () => restoreEcomProductColor(p.id, i))}><Undo2 size={12} />{text('用原图', 'Original')}</button>
            : c.transparent ? null
              : <button type="button" className="pf-es-link" disabled={busy === p.id} title={text('把实拍图抠成透明底，白底图和场景图都用它', 'Remove background')}
                onClick={() => void runCutout(p.id, [i])}><Scissors size={12} />{text('抠白底', 'Cut out')}</button>) : null}
          {p.colors.length > 1 ? <button type="button" className="pf-es-x" disabled={busy === p.id} aria-label={text('删除颜色', 'Remove color')}
            onClick={() => void act(p.id, () => removeEcomProductColor(p.id, i))}><X size={12} /></button> : null}
          <label className="pf-es-real">{c.real_image_url ? <img src={resolveToolMediaUrl(c.real_image_url)} alt="" /> : <ImagePlus size={14} />}<span>{c.real_image_url ? text('换实物图', 'Replace photo') : text('+ 实物图', '+ Photo')}</span>
            <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy === p.id} onChange={(e) => {
              const f = e.target.files?.[0]; e.target.value = ''
              if (f) void act(p.id, () => setEcomProductColorReal(p.id, i, f))
            }} />
          </label>
        </div>)}
        <label className="pf-es-add"><Plus size={16} /><span>{text('加颜色', 'Add color')}</span>
          <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={busy === p.id} onChange={(e) => {
            const f = e.target.files?.[0]; e.target.value = ''
            if (!f) return
            void (async () => {
              const name = (await dialog.prompt({ title: text('颜色名', 'Color name'), placeholder: text('如：湖绿', 'e.g. Teal') })) ?? ''
              await act(p.id, async () => {
                const next = await addEcomProductColor(p.id, name || '', f)
                if (colorsToCutout(next).includes(next.colors.length - 1)) void runCutout(next.id, [next.colors.length - 1])
                return next
              })
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
      <KnowledgePanel product={p} planFen={planFen} billingEnabled={billingEnabled}
        onChange={(next) => setItems((prev) => (prev || []).map((x) => (x.id === next.id ? next : x)))} />
    </div>)}
  </div>
}
