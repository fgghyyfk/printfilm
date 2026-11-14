import { useEffect, useState } from 'react'
import { Check, FolderOpen, Loader2, Sparkles, Star, Upload, X } from 'lucide-react'
import { generateToolModel, listToolModels, resolveToolMediaUrl, updateToolModel, uploadToolModel, type ToolModel } from '../../api/tools'
import { ECOM_UPLOAD_ACCEPT, formatFen, newlyAddedModelId, validateProductFile } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import ToolReferenceAssetPicker from '../tools/ToolReferenceAssetPicker'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'

type Props = {
  modelId: number | null
  onPick: (id: number | null) => void
  imageFen: number
  billing: boolean
}

const EXAMPLES = [
  '25 岁中国女生，鹅蛋脸，五官立体，黑色低盘发，气质温柔知性',
  '30 岁中国女性，短发干练，眉眼清冷，高级感',
  '22 岁中国女生，圆脸，笑容甜美，黑长直发',
]

/** 第②步：选模特（默认模特已选好）；可一句话生成新模特或从资产库加入 */
export default function ModelStep({ modelId, onPick, imageFen, billing }: Props) {
  const text = useGenerationText()
  /* 模特库、生成表单与状态 */
  const [models, setModels] = useState<ToolModel[] | null>(null)
  const [desc, setDesc] = useState('')
  const [asDefault, setAsDefault] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  // 已有模特时「生成新模特」默认收起，点开再填
  // 点模特卡片：放大预览，在预览里选用或设为默认
  const [preview, setPreview] = useState<ToolModel | null>(null)

  // 预览打开时按 Esc 关闭
  useEffect(() => {
    if (!preview) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPreview(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [preview])
  const [genOpen, setGenOpen] = useState(false)

  // 首次加载：未选时预选默认模特
  useEffect(() => {
    const controller = new AbortController()
    void listToolModels(controller.signal).then((r) => {
      setModels(r.items)
      if (modelId === null) onPick(r.items.find((m) => m.is_default)?.id ?? r.items[0]?.id ?? null)
    }).catch(() => { if (!controller.signal.aborted) setModels([]) })
    return () => controller.abort()
    // 只在进入本步时加载一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 一句话生成新模特
  async function generate() {
    if (desc.trim().length < 4) return setError(text('请用一句话描述模特', 'Describe the model'))
    setBusy('gen'); setError('')
    try {
      const r = await generateToolModel({ description: desc.trim(), make_default: asDefault })
      setModels(r.items)
      onPick(newlyAddedModelId(models || [], r.items))
      setDesc('')
    } catch (err) { setError(err instanceof Error ? err.message : text('生成失败', 'Failed')) }
    finally { setBusy('') }
  }

  // 直接上传模特照片（免费）
  async function upload(file: File | undefined) {
    if (!file) return
    if (validateProductFile(file)) return setError(text('图片仅支持 PNG / JPG / WebP，且不超过 10MB', 'PNG / JPG / WebP up to 10MB'))
    setBusy('upload'); setError('')
    try { const r = await uploadToolModel(file, !(models || []).some((m) => m.is_default)); setModels(r.items); onPick(newlyAddedModelId(models || [], r.items)) }
    catch (err) { setError(err instanceof Error ? err.message : text('上传失败', 'Upload failed')) }
    finally { setBusy('') }
  }

  // 设为默认 / 从资产库加入
  async function save(id: number, isDefault: boolean) {
    setBusy(`m${id}`); setError('')
    try { const r = await updateToolModel(id, { is_model: true, is_default: isDefault }); setModels(r.items); onPick(id) }
    catch (err) { setError(err instanceof Error ? err.message : text('保存失败', 'Failed')) }
    finally { setBusy('') }
  }

  if (!models) return <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /> {text('加载模特库…', 'Loading…')}</p>
  return <div className="pf-es-step">
    <div className="pf-es-grid">
      {models.map((m) => <div key={m.id} className={`pf-es-card${modelId === m.id ? ' active' : ''}`} role="button" tabIndex={0}
        onClick={() => setPreview(m)} onKeyDown={(e) => { if (e.key === 'Enter') setPreview(m) }}>
        <img src={resolveToolMediaUrl(m.cover || m.url)} alt="" />
        <strong>{m.name || text('模特', 'Model')}{modelId === m.id ? <span className="pf-es-tag"> <Check size={12} />{text('已选用', 'Selected')}</span> : null}</strong>
        {m.is_default ? <span className="pf-es-tag"><Star size={12} />{text('默认', 'Default')}</span>
          : <button type="button" className="pf-es-link" disabled={!!busy} onClick={(e) => { e.stopPropagation(); void save(m.id, true) }}>{text('设为默认', 'Set default')}</button>}
      </div>)}
      <label className="pf-es-card pf-es-card-new">
        {busy === 'upload' ? <Loader2 size={24} className="pf-tool-spin" /> : <Upload size={24} />}
        <strong>{text('上传模特照片', 'Upload model photo')}</strong>
        <span className="pf-muted">{text('正面清晰的半身照效果最好，免费', 'A clear half-body photo works best')}</span>
        <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} disabled={!!busy} onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = '' }} />
      </label>
      <button type="button" className="pf-es-card pf-es-card-new" onClick={() => setPickerOpen(true)}>
        <FolderOpen size={24} /><strong>{text('从资产库加入', 'From library')}</strong><span className="pf-muted">{text('用你已有的模特照片', 'Use an existing photo')}</span>
      </button>
    </div>
    {!models.length ? <p className="pf-muted">{text('还没有模特：在下面一句话生成一位，或从资产库加入。', 'No models yet — generate one below.')}</p> : null}
    {models.length && !genOpen ? <button type="button" className="pf-es-link" onClick={() => setGenOpen(true)}><Sparkles size={14} />{text('一句话生成新模特（收一张生图费）', 'Generate a new model')}</button> : null}
    {!models.length || genOpen ? <div className="pf-tool-panel pf-es-gen">
      <strong><Sparkles size={15} /> {text('一句话生成新模特', 'Generate a new model')}</strong>
      <textarea rows={2} maxLength={200} value={desc} disabled={!!busy} placeholder={text('年龄、长相、发型、气质…', 'Age, look, hair, vibe…')} onChange={(e) => setDesc(e.target.value)} />
      <div className="pf-tool-chips">{EXAMPLES.map((ex) => <button key={ex} type="button" disabled={!!busy} onClick={() => setDesc(ex)}>{ex.slice(0, 14)}…</button>)}</div>
      <label className="pf-es-check"><input type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />{text('生成后设为默认模特', 'Set as default')}</label>
      <div className="pf-es-actions">
        <Button variant="lime" size="sm" disabled={!!busy} onClick={() => void generate()}>{busy === 'gen' ? <><Loader2 size={14} className="pf-tool-spin" />{text('生成中，约 30 秒…', 'Generating…')}</> : text('生成模特', 'Generate')}</Button>
        {billing ? <span className="pf-muted">{text(`约 ${formatFen(imageFen)} / 位`, `~${formatFen(imageFen)}`)}</span> : null}
        {models.length ? <button type="button" className="pf-es-link" disabled={!!busy} onClick={() => setGenOpen(false)}>{text('收起', 'Hide')}</button> : null}
      </div>
    </div> : null}
    {error ? <BillingErrorNotice message={error} className="pf-tool-error" /> : null}
    {preview ? <div className="pf-es-preview" role="dialog" aria-modal="true" aria-label={preview.name || text('模特预览', 'Model preview')} onClick={() => setPreview(null)}>
      <button type="button" className="pf-es-preview-close" aria-label={text('关闭', 'Close')} onClick={() => setPreview(null)}><X size={20} /></button>
      <img src={resolveToolMediaUrl(preview.url || preview.cover)} alt={preview.name || ''} onClick={(e) => e.stopPropagation()} />
      <div className="pf-es-preview-bar" onClick={(e) => e.stopPropagation()}>
        <strong>{preview.name || text('模特', 'Model')}{preview.is_default ? text('（默认）', ' (default)') : ''}</strong>
        <div className="pf-es-actions">
          {!preview.is_default ? <Button variant="outline" size="sm" disabled={!!busy} onClick={() => { void save(preview.id, true); setPreview(null) }}><Star size={14} />{text('设为默认', 'Set default')}</Button> : null}
          <Button variant="lime" size="sm" onClick={() => { onPick(preview.id); setPreview(null) }}><Check size={14} />{modelId === preview.id ? text('已选用', 'Selected') : text('用这位模特', 'Use this model')}</Button>
        </div>
      </div>
    </div> : null}
    <ToolReferenceAssetPicker open={pickerOpen} onClose={() => setPickerOpen(false)} allowedKinds={['image']}
      onPick={(items) => { const first = items[0]; if (first) void save(first.id, !models.some((m) => m.is_default)) }} />
  </div>
}
