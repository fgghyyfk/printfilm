import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import type { EcomSetOptions, EcomSetPlanPayload } from '../../api/ecomSet'
import { listToolModels, resolveToolMediaUrl, type ToolModel } from '../../api/tools'
import { useI18n } from '../../i18n'
import { ECOM_UPLOAD_ACCEPT, formatFen, validateProductFile } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'

/** 表单预填值（重新规划时沿用上次的品名/平台/风格/张数） */
export type EcomSetFormInitial = Partial<Pick<EcomSetPlanPayload, 'name' | 'platform' | 'style' | 'count' | 'notes' | 'category' | 'scarfSize' | 'scarfMaterial' | 'modelAssetId'>>

type Props = {
  options: EcomSetOptions
  initial?: EcomSetFormInitial
  busy: boolean
  error: string
  onSubmit: (payload: EcomSetPlanPayload) => void
}

/** 第①步：商品图上传 + 品名/平台/风格/张数/卖点，提交生成方案 */
export default function EcomSetForm({ options, initial, busy, error, onSubmit }: Props) {
  const { t, m } = useI18n()
  /*
   * file 商品图；preview 本地预览地址
   * name 品名；platform/style 选项 id；count 张数；notes 补充卖点
   * localError 前端校验错误
   */
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [name, setName] = useState(initial?.name || '')
  const [platform, setPlatform] = useState(initial?.platform || options.platforms[0]?.id || '')
  const [style, setStyle] = useState(initial?.style || options.styles[0]?.id || '')
  const [count, setCount] = useState(initial?.count || (options.counts.includes(6) ? 6 : options.counts[0] || 6))
  const [notes, setNotes] = useState(initial?.notes || '')
  /* 丝巾详情页：品类、尺寸、模特（默认选中模特库里的默认模特） */
  const [category, setCategory] = useState<'general' | 'scarf'>(initial?.category === 'scarf' ? 'scarf' : 'general')
  const [scarfSize, setScarfSize] = useState(initial?.scarfSize || '88')
  const [scarfMaterial, setScarfMaterial] = useState(initial?.scarfMaterial || 'silk')
  const [scarfCount, setScarfCount] = useState(initial?.category === 'scarf' && initial.count ? initial.count : 8)
  const [models, setModels] = useState<ToolModel[] | null>(null)
  const [modelId, setModelId] = useState<number | null>(initial?.modelAssetId ?? null)
  const [localError, setLocalError] = useState('')
  const isScarf = category === 'scarf'

  // 选丝巾时加载模特库，未指定则用默认模特
  useEffect(() => {
    if (!isScarf || models) return
    const controller = new AbortController()
    void listToolModels(controller.signal).then(result => {
      setModels(result.items)
      setModelId(current => current ?? result.items.find(item => item.is_default)?.id ?? result.items[0]?.id ?? null)
    }).catch(() => { if (!controller.signal.aborted) setModels([]) })
    return () => controller.abort()
  }, [isScarf, models])
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview)
    }
  }, [preview])

  // 选中文件：校验格式与大小后生成预览
  function applyFile(next: File | undefined) {
    if (!next) return
    const invalid = validateProductFile(next)
    if (invalid) {
      setLocalError(invalid === 'type' ? t('ecomSet.errors.fileType') : t('ecomSet.errors.fileSize'))
      return
    }
    setLocalError('')
    setFile(next)
    setPreview(URL.createObjectURL(next))
  }

  // 文件选择框回调
  function onPick(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0]
    event.target.value = ''
    applyFile(picked)
  }

  // 拖拽上传
  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    if (busy) return
    applyFile(event.dataTransfer.files?.[0])
  }

  // 校验必填后提交
  function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!file) return setLocalError(t('ecomSet.errors.uploadImage'))
    if (!name.trim()) return setLocalError(t('ecomSet.errors.fillName'))
    if (isScarf && !modelId) return setLocalError(t('ecomSet.form.modelNone'))
    setLocalError('')
    onSubmit({ file, name: name.trim(), platform, style, count: isScarf ? scarfCount : count, notes: notes.trim(),
      category, ...(isScarf ? { scarfSize, scarfMaterial, modelAssetId: modelId } : {}) })
  }

  const platformNames = m.ecomSet.platforms as Record<string, string>
  const styleNames = m.ecomSet.styles as Record<string, string>
  const shownError = localError || error

  return (
    <form className="pf-tool-panel pf-ecom-form" onSubmit={submit}>
      {options.categories?.length ? <div className="pf-tool-field">
        <span>{t('ecomSet.form.category')}</span>
        <div className="pf-tool-chips">
          {options.categories.map((opt) => (
            <button key={opt.id} type="button" disabled={busy} className={category === opt.id ? 'active' : ''}
              onClick={() => setCategory(opt.id === 'scarf' ? 'scarf' : 'general')}>{opt.name}</button>
          ))}
        </div>
        {isScarf ? <span className="pf-muted">{t('ecomSet.form.categoryHint')}</span> : null}
      </div> : null}

      <div className="pf-tool-field">
        <span>{t('ecomSet.form.image')}</span>
        <div
          className="pf-tool-upload"
          role="button"
          tabIndex={0}
          aria-disabled={busy}
          onClick={() => !busy && fileRef.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === 'Enter' || e.key === ' ') && !busy) fileRef.current?.click()
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
        >
          {preview ? <img className="pf-ecom-upload-preview" src={preview} alt="" /> : null}
          {file ? `${file.name} · ${t('ecomSet.form.imageChange')}` : t('ecomSet.form.imageHint')}
        </div>
        {!file && initial?.name ? <span className="pf-muted">{t('ecomSet.form.reuploadHint')}</span> : null}
        <input ref={fileRef} type="file" hidden accept={ECOM_UPLOAD_ACCEPT} onChange={onPick} />
      </div>

      <label className="pf-tool-field">
        <span>{t('ecomSet.form.name')}</span>
        <input
          type="text"
          maxLength={40}
          placeholder={t('ecomSet.form.namePlaceholder')}
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      {isScarf ? <>
        <div className="pf-tool-field">
          <span>{t('ecomSet.form.scarfSize')}</span>
          <div className="pf-tool-chips">
            {(options.scarf_sizes || []).map((opt) => (
              <button key={opt.id} type="button" disabled={busy} className={scarfSize === opt.id ? 'active' : ''}
                onClick={() => setScarfSize(opt.id)}>{opt.name}</button>
            ))}
          </div>
        </div>
        <div className="pf-tool-field">
          <span>{t('ecomSet.form.scarfMaterial')}</span>
          <div className="pf-tool-chips">
            {(options.scarf_materials || []).map((opt) => (
              <button key={opt.id} type="button" disabled={busy} className={scarfMaterial === opt.id ? 'active' : ''}
                onClick={() => setScarfMaterial(opt.id)}>{opt.name}</button>
            ))}
          </div>
        </div>
        <div className="pf-tool-field">
          <span>{t('ecomSet.form.model')}</span>
          {models === null ? <span className="pf-muted"><Loader2 size={14} className="pf-tool-spin" aria-hidden /></span>
            : models.length ? <div className="pf-ecom-models">
              {models.map((item) => (
                <button key={item.id} type="button" disabled={busy} title={item.name || ''}
                  className={`pf-ecom-model${modelId === item.id ? ' active' : ''}`} aria-pressed={modelId === item.id}
                  onClick={() => setModelId(item.id)}>
                  <img src={resolveToolMediaUrl(item.cover || item.url)} alt={item.name || t('ecomSet.form.model')} />
                  <span>{item.is_default ? t('ecomSet.form.modelDefault') : (item.name || t('ecomSet.form.modelPick'))}</span>
                </button>
              ))}
            </div> : <span className="pf-muted">{t('ecomSet.form.modelNone')}</span>}
        </div>
        <div className="pf-tool-field">
          <span>{t('ecomSet.form.count')}</span>
          <div className="pf-tool-chips">
            {(options.scarf_counts || [6, 8, 10]).map((n) => (
              <button key={n} type="button" disabled={busy} className={scarfCount === n ? 'active' : ''}
                onClick={() => setScarfCount(n)}>{t('ecomSet.form.countUnit', { count: n })}</button>
            ))}
          </div>
        </div>
      </> : <>
      <div className="pf-tool-field">
        <span>{t('ecomSet.form.platform')}</span>
        <div className="pf-tool-chips">
          {options.platforms.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={busy}
              className={platform === opt.id ? 'active' : ''}
              onClick={() => setPlatform(opt.id)}
            >
              {platformNames[opt.id] || opt.name}
            </button>
          ))}
        </div>
      </div>

      <div className="pf-tool-field">
        <span>{t('ecomSet.form.style')}</span>
        <div className="pf-tool-chips">
          {options.styles.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={busy}
              className={style === opt.id ? 'active' : ''}
              onClick={() => setStyle(opt.id)}
            >
              {styleNames[opt.id] || opt.name}
            </button>
          ))}
        </div>
      </div>

      <div className="pf-tool-field">
        <span>{t('ecomSet.form.count')}</span>
        <div className="pf-tool-chips">
          {options.counts.map((n) => (
            <button
              key={n}
              type="button"
              disabled={busy}
              className={count === n ? 'active' : ''}
              onClick={() => setCount(n)}
            >
              {t('ecomSet.form.countUnit', { count: n })}
            </button>
          ))}
        </div>
      </div>
      </>}

      <label className="pf-tool-field">
        <span>{t('ecomSet.form.notes')}</span>
        <textarea
          rows={4}
          maxLength={500}
          placeholder={t('ecomSet.form.notesPlaceholder')}
          value={notes}
          disabled={busy}
          onChange={(e) => setNotes(e.target.value)}
        />
      </label>

      {shownError ? <BillingErrorNotice message={shownError} className="pf-tool-error" /> : null}

      <div className="pf-ecom-submit-row">
        <Button type="submit" variant="lime" disabled={busy}>
          {busy ? (
            <>
              <Loader2 size={16} className="pf-tool-spin" aria-hidden />
              {t('ecomSet.form.submitting')}
            </>
          ) : (
            t('ecomSet.form.submit')
          )}
        </Button>
        {options.billing_enabled ? (
          <span className="pf-ecom-price">{t('ecomSet.form.price', { price: formatFen(options.plan_fen) })}</span>
        ) : null}
      </div>
      {busy ? <p className="pf-muted pf-tool-panel-hint">{t('ecomSet.form.submittingHint')}</p> : null}
    </form>
  )
}
