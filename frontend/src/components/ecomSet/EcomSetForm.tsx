import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import type { EcomSetOptions, EcomSetPlanPayload } from '../../api/ecomSet'
import { useI18n } from '../../i18n'
import { ECOM_UPLOAD_ACCEPT, formatFen, validateProductFile, validateSlotSelection } from '../../lib/ecomSet'
import BillingErrorNotice from '../billing/BillingErrorNotice'
import Button from '../ui/Button'
import { resolveToolMediaUrl } from '../../api/tools'
import EcomSetParameters from './EcomSetParameters'
import { useRefineSource } from './useRefineSource'

/** 表单预填值（重新规划时沿用上次的品名/平台/风格/张数） */
export type EcomSetFormInitial = Partial<Pick<EcomSetPlanPayload, 'name' | 'platform' | 'style' | 'count' | 'notes' | 'slots' | 'language' | 'clarity'>>

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
  const refined = useRefineSource()
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
  /* slots 自定义模块；language 目标文案语言；clarity 生图与输出清晰度。 */
  const [slots, setSlots] = useState<string[] | undefined>(initial?.slots)
  const [language, setLanguage] = useState(initial?.language || 'zh')
  const [clarity, setClarity] = useState(initial?.clarity || '2k')
  const [notes, setNotes] = useState(initial?.notes || '')
  const [localError, setLocalError] = useState('')
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
    if (!file && !refined.source) return setLocalError(t('ecomSet.errors.uploadImage'))
    if (!name.trim()) return setLocalError(t('ecomSet.errors.fillName'))
    if (slots && !validateSlotSelection(slots, options.slots)) return setLocalError(t('ecomSet.enhanced.slotsError'))
    setLocalError('')
    onSubmit({ file: file || undefined, name: name.trim(), platform, style, count, slots, language, clarity, notes: notes.trim(),
      ...(!file && refined.source ? { source_job_id: refined.source.jobId, source_item_idx: refined.source.idx } : {}) })
  }

  const platformNames = m.ecomSet.platforms as Record<string, string>
  const styleNames = m.ecomSet.styles as Record<string, string>
  const shownError = localError || error || (!file ? refined.error : '')
  const shownPreview = preview || (refined.source ? resolveToolMediaUrl(refined.source.url) : '')

  return (
    <form className="pf-tool-panel pf-ecom-form" onSubmit={submit}>
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
          {shownPreview ? <img className="pf-ecom-upload-preview" src={shownPreview} alt="" /> : null}
          {file ? `${file.name} · ${t('ecomSet.form.imageChange')}` : refined.source ? t('ecomSet.enhanced.sourceReady') : t('ecomSet.form.imageHint')}
        </div>
        {!file && refined.loading ? <span className="pf-muted">{t('ecomSet.enhanced.sourceLoading')}</span> : null}
        {!file && refined.error ? <Button type="button" variant="text" size="sm" onClick={refined.retry}>{t('ecomSet.retry')}</Button> : null}
        <Button size="sm" variant="text" to="/ecom/refine">{t('ecomSet.enhanced.noWhite')}</Button>
        {!file && !refined.source && initial?.name ? <span className="pf-muted">{t('ecomSet.form.reuploadHint')}</span> : null}
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
              className={!slots && count === n ? 'active' : ''}
              onClick={() => { setCount(n); setSlots(undefined) }}
            >
              {t('ecomSet.form.countUnit', { count: n })}
            </button>
          ))}
        </div>
      </div>

      <EcomSetParameters options={options} slots={slots} language={language} clarity={clarity} busy={busy}
        onSlots={setSlots} onLanguage={setLanguage} onClarity={setClarity} />

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
        <Button type="submit" variant="lime" disabled={busy || (!file && refined.loading)}>
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
