import { useState } from 'react'
import { BookOpen, ChevronDown, ChevronUp, Loader2, Sparkles, Upload, X } from 'lucide-react'
import {
  addEcomProductReferences, extractEcomProductKnowledge, removeEcomProductReference, saveEcomProductKnowledge,
  type EcomProduct,
} from '../../api/ecomProducts'
import { resolveToolMediaUrl } from '../../api/tools'
import { ECOM_UPLOAD_ACCEPT, formatFen } from '../../lib/ecomSet'
import {
  fromKnowledgeDraft, knowledgeCount, KNOWLEDGE_SECTIONS, toKnowledgeDraft, type KnowledgeDraft,
} from '../../lib/ecomKnowledge'
import { useGenerationText } from '../../lib/generationMedia'
import { dialog } from '../../lib/dialog'
import Button from '../ui/Button'

type Props = {
  product: EcomProduct
  /** 读图单价（分/批），用于提示 */
  planFen: number
  billingEnabled: boolean
  onChange: (next: EcomProduct) => void
}

/** 商品资料：上传参考详情（自己的 / 竞品）→ AI 整理 → 人工核对修改；出图文案和信息屏都从这里取 */
export default function KnowledgePanel({ product, planFen, billingEnabled, onChange }: Props) {
  const text = useGenerationText()
  const refs = product.references || []
  const count = knowledgeCount(product.knowledge)
  /* 展开 / 操作状态 */
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  /* 资料编辑稿（展开时从商品资料初始化） */
  const [draft, setDraft] = useState<KnowledgeDraft>(() => toKnowledgeDraft(product.knowledge))
  const [dirty, setDirty] = useState(false)

  // 执行一次操作，成功后用返回的商品刷新编辑稿
  async function run(key: string, fn: () => Promise<EcomProduct>) {
    setBusy(key); setError('')
    try {
      const next = await fn()
      onChange(next)
      setDraft(toKnowledgeDraft(next.knowledge)); setDirty(false)
    } catch (err) { setError(err instanceof Error ? err.message : text('操作失败', 'Failed')) }
    finally { setBusy('') }
  }

  // 上传参考详情
  function upload(kind: 'own' | 'competitor', files: FileList | null) {
    const list = Array.from(files || [])
    if (list.length) void run(`up-${kind}`, () => addEcomProductReferences(product.id, kind, list))
  }

  // AI 整理（收费前确认；已改未保存时提醒）
  async function extract() {
    const tip = billingEnabled
      ? text(`按读图批次计费，每批约 ${formatFen(planFen)}（一张长详情约 1～3 批）。`, 'Billed per batch.')
      : ''
    const warn = dirty ? text('当前修改还没保存，整理后会被覆盖。', 'Unsaved edits will be replaced. ') : ''
    if (!(await dialog.confirm(text(`AI 读取参考详情整理商品资料？${tip}${warn}已有资料会被补充，不会清空。`, `Extract product facts?${tip}`)))) return
    await run('extract', () => extractEcomProductKnowledge(product.id))
  }

  function edit(key: keyof KnowledgeDraft, value: string) {
    setDraft((d) => ({ ...d, [key]: value })); setDirty(true)
  }

  return <div className="pf-es-kb">
    <div className="pf-es-product-head">
      <div>
        <strong><BookOpen size={14} /> {text('商品资料', 'Product facts')}</strong>
        <span className="pf-muted"> · {refs.length ? text(`参考详情 ${refs.length} 张`, `${refs.length} references`) : text('未上传参考详情', 'No references')}
          {count ? text(` · 已整理 ${count} 条`, ` · ${count} facts`) : ''}</span>
      </div>
      <button type="button" className="pf-es-link" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}{open ? text('收起', 'Collapse') : text('上传详情 / 编辑资料', 'Edit')}
      </button>
    </div>
    {open ? <>
      <p className="pf-muted">{text('上传品牌原来的详情页切片（或长图），AI 会读出卖点、成分、香调、规格、用法和品牌故事；出图文案只用这里的内容，信息屏（多款一览、成分说明等）也从这里取。竞品详情只学讲述顺序和打动角度，不抄原文。',
        'Upload detail pages; AI extracts facts used for copy and info screens.')}</p>
      <div className="pf-es-kb-refs">
        {refs.map((r, i) => <div key={`${r.url}-${i}`} className="pf-es-kb-ref">
          <img src={resolveToolMediaUrl(r.url)} alt="" loading="lazy" />
          <em>{r.kind === 'competitor' ? text('竞品', 'Rival') : text('自己的', 'Own')}</em>
          <button type="button" className="pf-es-x" disabled={!!busy} aria-label={text('删除', 'Remove')}
            onClick={() => void run(`rm-${i}`, () => removeEcomProductReference(product.id, i))}><X size={12} /></button>
        </div>)}
        {(['own', 'competitor'] as const).map((kind) => <label key={kind} className="pf-es-add">
          {busy === `up-${kind}` ? <Loader2 size={16} className="pf-tool-spin" /> : <Upload size={16} />}
          <span>{kind === 'own' ? text('+ 自己的详情', '+ Own page') : text('+ 竞品详情', '+ Competitor')}</span>
          <input type="file" hidden multiple accept={ECOM_UPLOAD_ACCEPT} disabled={!!busy}
            onChange={(e) => { const f = e.target.files; upload(kind, f); e.target.value = '' }} />
        </label>)}
      </div>
      <div className="pf-es-actions">
        <Button variant="lime" size="sm" disabled={!refs.length || !!busy} onClick={() => void extract()}>
          {busy === 'extract' ? <Loader2 size={14} className="pf-tool-spin" /> : <Sparkles size={14} />}
          {busy === 'extract' ? text('正在读图整理（约 1 分钟）…', 'Reading…') : text('AI 整理资料', 'Extract with AI')}
        </Button>
      </div>
      {error ? <p className="pf-tool-error">{error}</p> : null}
      {product.knowledge?.warnings?.length ? <ul className="pf-es-kb-warn">
        {product.knowledge.warnings.map((w) => <li key={w}>{w}</li>)}
      </ul> : null}
      <div className="pf-es-kb-grid">
        <label>{text('一句话定位', 'Summary')}<input className="pf-ecom-input" value={draft.summary} maxLength={60}
          onChange={(e) => edit('summary', e.target.value)} /></label>
        <label>{text('英文名', 'English name')}<input className="pf-ecom-input" value={draft.en_name} maxLength={40}
          onChange={(e) => edit('en_name', e.target.value)} /></label>
        {KNOWLEDGE_SECTIONS.map((s) => <label key={s.key}>{s.label}<small>{s.hint}</small>
          <textarea value={draft[s.key]} onChange={(e) => edit(s.key, e.target.value)} /></label>)}
        <label>{text('品牌故事', 'Brand story')}<small>{text('≤240 字', '≤240 chars')}</small>
          <textarea value={draft.story} maxLength={240} onChange={(e) => edit('story', e.target.value)} /></label>
      </div>
      <div className="pf-es-actions">
        <Button variant="outline" size="sm" disabled={!dirty || !!busy}
          onClick={() => void run('save', () => saveEcomProductKnowledge(product.id, fromKnowledgeDraft(draft)))}>
          {busy === 'save' ? <Loader2 size={14} className="pf-tool-spin" /> : null}{text('保存资料', 'Save')}
        </Button>
        {dirty ? <span className="pf-muted">{text('有未保存的修改', 'Unsaved changes')}</span> : null}
      </div>
    </> : null}
  </div>
}
