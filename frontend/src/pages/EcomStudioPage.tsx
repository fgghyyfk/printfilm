import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, CheckCircle2, CircleAlert, Loader2, Wand2 } from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import Stepper from '../components/ui/Stepper'
import Button from '../components/ui/Button'
import BillingErrorNotice from '../components/billing/BillingErrorNotice'
import ProductStep from '../components/ecomStudio/ProductStep'
import ModelStep from '../components/ecomStudio/ModelStep'
import SlotStep from '../components/ecomStudio/SlotStep'
import ProductsTab from '../components/ecomStudio/ProductsTab'
import EcomSetHistory from '../components/ecomSet/EcomSetHistory'
import { getEcomSetOptions, planEcomSet, type EcomSetOptions } from '../api/ecomSet'
import type { EcomProduct } from '../api/ecomProducts'
import { resolveToolMediaUrl } from '../api/tools'
import { formatFen } from '../lib/ecomSet'
import { useGenerationText } from '../lib/generationMedia'
import '../styles/tools-base.css'
import '../components/ecomSet/ecomSet.css'
import '../components/ecomStudio/ecomStudio.css'

type Tab = 'make' | 'products' | 'history'

/**
 * 电商图：四步出图（选商品 → 选模特 → 选要哪些图 → 确认），全程不用写提示词。
 * 确认后生成方案，进入套图页（/daihuo?id=）改文案、生成、下载 ZIP / 长图、同款换色。
 */
export default function EcomStudioPage() {
  const text = useGenerationText()
  const nav = useNavigate()
  /* 选项与页签 */
  const [options, setOptions] = useState<EcomSetOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [tab, setTab] = useState<Tab>('make')
  /* 四步选择 */
  const [step, setStep] = useState(0)
  const [product, setProduct] = useState<EcomProduct | null>(null)
  const [colorIndex, setColorIndex] = useState(0)
  const [modelId, setModelId] = useState<number | null>(null)
  const [frame, setFrame] = useState('taobao_34')
  const [slots, setSlots] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  /* 提交 */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void getEcomSetOptions().then(setOptions).catch((err) => setOptionsError(err instanceof Error ? err.message : text('加载失败', 'Load failed')))
  }, [text])

  const categories = useMemo(() => options?.styled || [], [options])
  const category = categories.find((c) => c.id === product?.category) || categories[0]
  const needsModel = slots.some((k) => category?.slots.find((s) => s.key === k)?.ref === 'model')
  const billable = slots.filter((k) => !category?.slots.find((s) => s.key === k)?.free).length
  const total = (options?.plan_fen || 0) + billable * (options?.image_fen || 0)

  // 选商品：换品类时默认勾上该品类的默认整套
  function pickProduct(p: EcomProduct, ci: number) {
    const changed = p.category !== product?.category
    setProduct(p); setColorIndex(ci)
    const cat = categories.find((c) => c.id === p.category)
    if (cat && (changed || !slots.length)) setSlots(cat.compositions[String(cat.default_count)] || [])
  }

  const canNext = [Boolean(product), !needsModel || Boolean(modelId), slots.length > 0, true][step]

  // 生成方案并进入套图页
  async function submit() {
    if (!product || !category) return
    setBusy(true); setError('')
    try {
      const set = await planEcomSet({
        name: product.name, platform: '', style: 'minimal', count: slots.length, notes: notes.trim(),
        category: product.category, productId: product.id, colorIndex, modelAssetId: needsModel ? modelId : null,
        frame, slots,
      })
      nav(`/daihuo?id=${set.id}`)
    } catch (err) { setError(err instanceof Error ? err.message : text('生成方案失败', 'Planning failed')) }
    finally { setBusy(false) }
  }

  const steps = [text('选商品', 'Product'), text('选模特', 'Model'), text('选图', 'Images'), text('确认', 'Confirm')]
    .map((label, i) => ({ key: String(i), label }))

  return <AppShell active="image">
    <div className="pf-tool-detail pf-ecom-page pf-es-page">
      <div className="pf-ecom-head">
        <div>
          <h1>{text('电商图', 'Product images')}</h1>
          <p className="pf-muted">{text('上传商品，选模特和要的图，一键出整套详情页或单张上身图，不用写提示词。', 'Pick product, model and shots — no prompts needed.')}</p>
        </div>
        <Link to="/tools/image" className="pf-es-link">{text('高级生成（自己写提示词）', 'Advanced generation')} <ArrowRight size={14} /></Link>
      </div>
      <div className="pf-tool-chips pf-es-tabs">
        {([['make', text('做图', 'Create')], ['products', text('我的商品', 'My products')], ['history', text('历史套图', 'History')]] as [Tab, string][])
          .map(([id, label]) => <button key={id} type="button" className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {optionsError ? <p className="pf-tool-error">{optionsError}</p> : !options ? <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /></p>
        : tab === 'products' ? <ProductsTab categories={categories} onUse={(p) => { pickProduct(p, 0); setTab('make'); setStep(1) }} />
          : tab === 'history' ? <EcomSetHistory currentId={null} refreshKey={0} onOpen={(id) => nav(`/daihuo?id=${id}`)} />
            : <>
              <div className="pf-ecom-steps"><Stepper steps={steps} current={step} /></div>
              {step === 0 ? <ProductStep categories={categories} product={product} colorIndex={colorIndex} onPick={pickProduct} /> : null}
              {step === 1 ? <ModelStep modelId={modelId} onPick={setModelId} imageFen={options.image_fen} billing={options.billing_enabled} /> : null}
              {step === 2 && category ? <SlotStep category={category} frames={options.frames || []} frame={frame} slots={slots} onFrame={setFrame} onSlots={setSlots} /> : null}
              {step === 3 && product && category ? <div className="pf-tool-panel pf-es-confirm">
                <div className="pf-es-summary">
                  <img src={resolveToolMediaUrl(product.colors[colorIndex]?.image_url)} alt="" />
                  <dl>
                    <dt>{text('商品', 'Product')}</dt><dd>{product.name} · {product.colors[colorIndex]?.name} · {[category.name, product.material_label, product.size].filter(Boolean).join(' · ')}</dd>
                    <dt>{text('画幅', 'Format')}</dt><dd>{options.frames?.find((f) => f.id === frame)?.name}</dd>
                    <dt>{text('张数', 'Images')}</dt><dd>{text(`${slots.length} 张（${slots.length - billable} 张免费）`, `${slots.length}`)}</dd>
                    <dt>{text('模特', 'Model')}</dt><dd>{needsModel ? text('已选', 'Selected') : text('不需要', 'Not needed')}</dd>
                  </dl>
                </div>
                <div className="pf-es-checklist">
                  <strong>{text('素材检查', 'Inputs')}</strong>
                  {[
                    { ok: Boolean(product.colors[colorIndex]?.image_url), label: text('设计图', 'Design image'), tip: text('出图的图案和颜色以它为准', 'Pattern & color source') },
                    { ok: Boolean(product.fabric_image_url), label: text('材质实拍', 'Fabric photo'), tip: text('没有时质感靠文字描述，容易偏；建议到「我的商品」补一张近拍', 'Add a close-up in My products for accurate texture') },
                    { ok: !needsModel || Boolean(modelId), label: text('模特图', 'Model'), tip: text('所有上身图都以这位模特为原型', 'Every on-model shot uses this model') },
                    { ok: Boolean(product.notes.trim() || notes.trim()), label: text('成分与卖点', 'Selling points'), tip: text('没有时文案会比较泛，建议写成分、工艺', 'Add composition & craft for better copy') },
                  ].map((row) => <div key={row.label} className={`pf-es-check-row${row.ok ? ' ok' : ''}`}>
                    {row.ok ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}<span>{row.label}</span><small className="pf-muted">{row.ok ? text('已就绪', 'Ready') : row.tip}</small>
                  </div>)}
                  {!product.fabric_image_url ? <button type="button" className="pf-es-link" onClick={() => setTab('products')}>{text('去「我的商品」补材质实拍 →', 'Add fabric photo →')}</button> : null}
                </div>
                <label className="pf-tool-field"><span>{text('这次想强调的卖点（选填）', 'Extra notes (optional)')}</span>
                  <textarea rows={2} maxLength={300} value={notes} placeholder={text('留空则用商品资料里的成分与卖点', 'Defaults to product notes')} onChange={(e) => setNotes(e.target.value)} />
                </label>
                {options.billing_enabled ? <p className="pf-ecom-price">{text(`预计 ${formatFen(total)}（方案 ${formatFen(options.plan_fen)} + ${billable} 张 × ${formatFen(options.image_fen)}）`, `~${formatFen(total)}`)}</p> : null}
                <p className="pf-muted">{text('下一步会先出方案：每张图的画面与文案都可以改，确认后才开始生成。', 'You can edit the plan before generating.')}</p>
                {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
              </div> : null}
              <div className="pf-es-actions pf-es-nav">
                <Button variant="outline" disabled={step === 0 || busy} onClick={() => setStep((s) => s - 1)}><ArrowLeft size={14} />{text('上一步', 'Back')}</Button>
                {step < 3 ? <Button variant="lime" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>{text('下一步', 'Next')}<ArrowRight size={14} /></Button>
                  : <Button variant="lime" disabled={busy} onClick={() => void submit()}>{busy ? <><Loader2 size={16} className="pf-tool-spin" />{text('正在出方案，约 20 秒…', 'Planning…')}</> : <><Wand2 size={16} />{text('生成方案', 'Create plan')}</>}</Button>}
              </div>
            </>}
    </div>
  </AppShell>
}
