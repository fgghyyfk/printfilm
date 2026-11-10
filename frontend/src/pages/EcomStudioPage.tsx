import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, CheckCircle2, CircleAlert, Loader2, Wand2 } from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import Stepper from '../components/ui/Stepper'
import Button from '../components/ui/Button'
import BillingErrorNotice from '../components/billing/BillingErrorNotice'
import ProductStep from '../components/ecomStudio/ProductStep'
import ModelStep from '../components/ecomStudio/ModelStep'
import SlotStep from '../components/ecomStudio/SlotStep'
import ElementPicker from '../components/ecomStudio/ElementPicker'
import type { EcomElement } from '../api/ecomElements'
import SceneStep from '../components/ecomStudio/SceneStep'
import ProductsTab from '../components/ecomStudio/ProductsTab'
import EcomSetBatches from '../components/ecomSet/EcomSetBatches'
import { getEcomSetOptions, planEcomSet, type EcomSetOptions } from '../api/ecomSet'
import { getEcomProduct, type EcomProduct } from '../api/ecomProducts'
import { resolveToolMediaUrl } from '../api/tools'
import { formatFen, parseSetId, reconcileColorIndex } from '../lib/ecomSet'
import { defaultSceneIndustry, loadScenePref, saveScenePref } from '../lib/ecomScenePrefs'
import { useGenerationText } from '../lib/generationMedia'
import '../styles/tools-base.css'
import '../components/ecomSet/ecomSet.css'
import '../components/ecomStudio/ecomStudio.css'

type Tab = 'make' | 'products' | 'history'
type StepKey = 'product' | 'scene' | 'model' | 'slots' | 'confirm'

/**
 * 电商图：分步出图（选商品 → 选场景 →〔选模特，日化标品跳过〕→ 选要哪些图 → 确认），全程不用写提示词。
 * 确认后生成方案，进入套图页（/daihuo?id=）改文案、生成、下载 ZIP / 长图、同款换色。
 */
export default function EcomStudioPage() {
  const text = useGenerationText()
  const nav = useNavigate()
  /* 重新规划带来的 ?product=&color=：选项就绪后预选一次 */
  const [params, setParams] = useSearchParams()
  const replanApplied = useRef(false)
  /* 选项与页签 */
  const [options, setOptions] = useState<EcomSetOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [tab, setTab] = useState<Tab>('make')
  /* 分步选择 */
  const [step, setStep] = useState(0)
  const [product, setProduct] = useState<EcomProduct | null>(null)
  const [colorIndex, setColorIndex] = useState(0)
  const [modelId, setModelId] = useState<number | null>(null)
  const [frame, setFrame] = useState('taobao_34')
  const [slots, setSlots] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [engine, setEngine] = useState('seedream')
  const [elements, setElements] = useState<EcomElement[]>([])
  /* 场景：行业标签与所选场景（空 key = 不指定场景） */
  const [industry, setIndustry] = useState('')
  const [scene, setScene] = useState<{ key: string; name: string } | null>(null)
  /* 提交 */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void getEcomSetOptions().then(setOptions).catch((err) => setOptionsError(err instanceof Error ? err.message : text('加载失败', 'Load failed')))
  }, [text])

  const categories = useMemo(() => options?.styled || [], [options])
  const category = categories.find((c) => c.id === product?.category) || categories[0]
  const usesModel = category?.uses_model !== false
  const needsModel = usesModel && slots.some((k) => category?.slots.find((s) => s.key === k)?.ref === 'model')
  const isGoods = category?.id === 'goods'
  const industries = useMemo(() => options?.industries || [], [options])
  const billable = slots.filter((k) => !category?.slots.find((s) => s.key === k)?.free).length
  const engines = options?.image_engines || []
  const unitFen = engines.find((e) => e.id === engine)?.image_fen ?? options?.image_fen ?? 0
  const total = (options?.plan_fen || 0) + billable * unitFen

  // 选商品：换品类时默认勾上该品类的默认整套，并恢复该品类上次的行业与场景
  function pickProduct(p: EcomProduct, ci: number) {
    const changed = p.category !== product?.category
    setError('')
    setProduct(p); setColorIndex(ci)
    const cat = categories.find((c) => c.id === p.category)
    if (cat && (changed || !slots.length)) setSlots(cat.compositions[String(cat.default_count)] || [])
    if (changed || !industry) {
      const pref = loadScenePref(p.category)
      setIndustry(defaultSceneIndustry(p.category, industries, options?.category_industry))
      setScene(pref?.sceneKey ? { key: pref.sceneKey, name: pref.sceneName } : null)
    }
  }

  // 重新规划回到本页：预选原商品与颜色款，直接进入选模特；商品已删除时留在第一步并提示
  useEffect(() => {
    const productId = parseSetId(params.get('product'))
    if (!productId || !options || replanApplied.current) return
    replanApplied.current = true
    const color = Number(params.get('color')) || 0
    setParams({}, { replace: true })
    void getEcomProduct(productId)
      .then((p) => { pickProduct(p, p.colors[color] ? color : 0); setStep(1) })
      .catch((err) => setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')))
    // pickProduct 依赖当前选择，这里只在选项就绪后执行一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, params])

  // 回到「做图」时同步商品最新资料（可能刚在「我的商品」补图、删色或删除），避免用旧快照出图
  const currentProductId = product?.id
  useEffect(() => {
    if (tab !== 'make' || !currentProductId) return
    const previous = product?.colors || []
    const controller = new AbortController()
    void getEcomProduct(currentProductId, controller.signal).then((fresh) => {
      setColorIndex((index) => reconcileColorIndex(previous, index, fresh.colors))
      setProduct(fresh)
    }).catch((err) => {
      if (controller.signal.aborted || !(err instanceof Error) || err.message !== '商品不存在') return
      setProduct(null); setStep(0)
      setError(text('这款商品已被删除，请重新选择', 'This product was deleted. Please pick another.'))
    })
    return () => controller.abort()
    // 只在切回「做图」或换商品时同步；product 本身会被这里更新，不能作为依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, currentProductId, text])

  // 切换行业：记住选择
  function pickIndustry(id: string) {
    setIndustry(id)
    if (product) saveScenePref(product.category, { industry: id, sceneKey: scene?.key || '', sceneName: scene?.name || '' })
  }

  // 选场景：记住选择（null = 不指定场景）
  function pickScene(next: { key: string; name: string } | null) {
    setScene(next)
    if (product) saveScenePref(product.category, { industry, sceneKey: next?.key || '', sceneName: next?.name || '' })
  }

  /* 步骤流：不用模特的品类（日化标品）跳过选模特 */
  const stepKeys: StepKey[] = usesModel ? ['product', 'scene', 'model', 'slots', 'confirm'] : ['product', 'scene', 'slots', 'confirm']
  const stepKey = stepKeys[Math.min(step, stepKeys.length - 1)]
  const lastStep = stepKeys.length - 1
  const canNext = { product: Boolean(product), scene: true, model: !needsModel || Boolean(modelId), slots: slots.length > 0, confirm: true }[stepKey]

  // 生成方案并进入套图页
  async function submit() {
    if (!product || !category) return
    setBusy(true); setError('')
    try {
      const set = await planEcomSet({
        name: product.name, platform: '', style: 'minimal', count: slots.length, notes: notes.trim(),
        category: product.category, productId: product.id, colorIndex, modelAssetId: needsModel ? modelId : null,
        frame, slots, sceneKey: scene?.key || '', imageEngine: engine, elementKeys: elements.map((e) => e.key),
      })
      nav(`/daihuo?id=${set.id}`)
    } catch (err) { setError(err instanceof Error ? err.message : text('生成方案失败', 'Planning failed')) }
    finally { setBusy(false) }
  }

  const stepLabels: Record<StepKey, string> = {
    product: text('选商品', 'Product'), scene: text('选场景', 'Scene'), model: text('选模特', 'Model'),
    slots: text('选图', 'Images'), confirm: text('确认', 'Confirm'),
  }
  const steps = stepKeys.map((key) => ({ key, label: stepLabels[key] }))

  return <AppShell active="image">
    <div className="pf-tool-detail pf-ecom-page pf-es-page">
      <div className="pf-ecom-head">
        <div>
          <h1>{text('电商图', 'Product images')}</h1>
          <p className="pf-muted">{text('上传商品，选场景、模特和要的图，一键出整套详情页或单张上身图，不用写提示词。', 'Pick product, scene, model and shots — no prompts needed.')}</p>
        </div>
        <Link to="/tools/image" className="pf-es-link">{text('高级生成（自己写提示词）', 'Advanced generation')} <ArrowRight size={14} /></Link>
      </div>
      <div className="pf-tool-chips pf-es-tabs">
        {([['make', text('做图', 'Create')], ['products', text('我的商品', 'My products')], ['history', text('历史套图', 'History')]] as [Tab, string][])
          .map(([id, label]) => <button key={id} type="button" className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {optionsError ? <p className="pf-tool-error">{optionsError}</p> : !options ? <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /></p>
        : tab === 'products' ? <ProductsTab categories={categories} planFen={options.plan_fen} billingEnabled={options.billing_enabled} onUse={(p) => { pickProduct(p, 0); setTab('make'); setStep(1) }} />
          : tab === 'history' ? <EcomSetBatches onOpen={(id) => nav(`/daihuo?id=${id}`)} />
            : <>
              <div className="pf-ecom-steps"><Stepper steps={steps} current={step} /></div>
              {stepKey === 'product' && error ? <p className="pf-tool-error" role="alert">{error}</p> : null}
              {stepKey === 'product' ? <ProductStep categories={categories} product={product} colorIndex={colorIndex} onPick={pickProduct} /> : null}
              {stepKey === 'scene' && product ? <SceneStep industries={industries} industry={industry || industries[0]?.id || 'daily'}
                onIndustry={pickIndustry} sceneKey={scene?.key || ''} onPick={pickScene} /> : null}
              {stepKey === 'scene' && product ? <ElementPicker productId={product.id} industry={industry || industries[0]?.id || ''} sceneKey={scene?.key || ''}
                value={elements} onChange={setElements} /> : null}
              {stepKey === 'model' ? <ModelStep modelId={modelId} onPick={setModelId} imageFen={options.image_fen} billing={options.billing_enabled} /> : null}
              {stepKey === 'slots' && category ? <SlotStep category={category} frames={options.frames || []} frame={frame} slots={slots} onFrame={setFrame} onSlots={setSlots}
                engines={engines} engine={engine} onEngine={setEngine} /> : null}
              {stepKey === 'confirm' && product && category ? <div className="pf-tool-panel pf-es-confirm">
                <div className="pf-es-summary">
                  <img src={resolveToolMediaUrl(product.colors[colorIndex]?.image_url)} alt="" />
                  <dl>
                    <dt>{text('商品', 'Product')}</dt><dd>{product.name} · {product.colors[colorIndex]?.name} · {[category.name, product.material_label, product.size].filter(Boolean).join(' · ')}</dd>
                    <dt>{text('场景', 'Scene')}</dt><dd>{scene ? scene.name || scene.key : text('不指定（按品类默认画面）', 'Category default')}</dd>
                    <dt>{text('画幅', 'Format')}</dt><dd>{options.frames?.find((f) => f.id === frame)?.name}</dd>
                    <dt>{text('张数', 'Images')}</dt><dd>{text(`${slots.length} 张（${slots.length - billable} 张免费）`, `${slots.length}`)}</dd>
                    {usesModel ? <><dt>{text('模特', 'Model')}</dt><dd>{needsModel ? text('已选', 'Selected') : text('不需要', 'Not needed')}</dd></> : null}
                  </dl>
                </div>
                <div className="pf-es-checklist">
                  <strong>{text('素材检查', 'Inputs')}</strong>
                  {(isGoods ? [
                    { ok: Boolean(product.colors[colorIndex]?.image_url), label: text('透明底商品图', 'Transparent product PNG'), tip: text('商品会原样合成进场景；普通照片也能用，但商品可能被重绘', 'Composited as-is into the scene') },
                    { ok: Boolean(product.material_label), label: text('包装形态', 'Packaging'), tip: text('决定台面摆放与道具比例，可到「我的商品」补上', 'Set it in My products') },
                    { ok: Boolean(product.notes.trim() || notes.trim()), label: text('成分与卖点', 'Selling points'), tip: text('没有时文案会比较泛，建议写核心成分、功效、规格', 'Add ingredients & benefits for better copy') },
                  ] : [
                    { ok: Boolean(product.colors[colorIndex]?.image_url), label: text('设计图', 'Design image'), tip: text('出图的图案和颜色以它为准', 'Pattern & color source') },
                    { ok: Boolean(product.colors[colorIndex]?.real_image_url), label: text('实物图（选填）', 'Real photo (optional)'), tip: text('有实物图时，真实光线下的颜色和质感更准；可到「我的商品」给这个颜色加一张', 'Add one in My products for true color') },
                    { ok: Boolean(product.fabric_image_url), label: text('材质实拍', 'Fabric photo'), tip: text('没有时质感靠文字描述，容易偏；建议到「我的商品」补一张近拍', 'Add a close-up in My products for accurate texture') },
                    { ok: !needsModel || Boolean(modelId), label: text('模特图', 'Model'), tip: text('所有上身图都以这位模特为原型', 'Every on-model shot uses this model') },
                    { ok: Boolean(product.notes.trim() || notes.trim()), label: text('成分与卖点', 'Selling points'), tip: text('没有时文案会比较泛，建议写成分、工艺', 'Add composition & craft for better copy') },
                  ]).map((row) => <div key={row.label} className={`pf-es-check-row${row.ok ? ' ok' : ''}`}>
                    {row.ok ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}<span>{row.label}</span><small className="pf-muted">{row.ok ? text('已就绪', 'Ready') : row.tip}</small>
                  </div>)}
                  {!isGoods && !product.fabric_image_url ? <button type="button" className="pf-es-link" onClick={() => setTab('products')}>{text('去「我的商品」补材质实拍 →', 'Add fabric photo →')}</button> : null}
                </div>
                <label className="pf-tool-field"><span>{text('这次想强调的卖点（选填）', 'Extra notes (optional)')}</span>
                  <textarea rows={2} maxLength={300} value={notes} placeholder={text('留空则用商品资料里的成分与卖点', 'Defaults to product notes')} onChange={(e) => setNotes(e.target.value)} />
                </label>
                {options.billing_enabled ? <p className="pf-ecom-price">{text(`预计 ${formatFen(total)}（方案 ${formatFen(options.plan_fen)} + ${billable} 张 × ${formatFen(unitFen)}）`, `~${formatFen(total)}`)}</p> : null}
                <p className="pf-muted">{text('下一步会先出方案：每张图的画面与文案都可以改，确认后才开始生成。', 'You can edit the plan before generating.')}</p>
                {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
              </div> : null}
              <div className="pf-es-actions pf-es-nav">
                <Button variant="outline" disabled={step === 0 || busy} onClick={() => setStep((s) => s - 1)}><ArrowLeft size={14} />{text('上一步', 'Back')}</Button>
                {step < lastStep ? <Button variant="lime" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>{text('下一步', 'Next')}<ArrowRight size={14} /></Button>
                  : <Button variant="lime" disabled={busy} onClick={() => void submit()}>{busy ? <><Loader2 size={16} className="pf-tool-spin" />{text('正在出方案，约 20 秒…', 'Planning…')}</> : <><Wand2 size={16} />{text('生成方案', 'Create plan')}</>}</Button>}
              </div>
            </>}
    </div>
  </AppShell>
}
