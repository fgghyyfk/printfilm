import { useId, useRef, useState, type FormEvent } from 'react'
import { ArrowUpRight, Check, Clapperboard, LayoutGrid, LoaderCircle, Sparkles } from 'lucide-react'
import Modal from '../ui/Modal'
import Button from '../ui/Button'
import { DramaImageStyleModal } from '../../pages/drama/DramaImageStyleModal'
import { EPISODE_COUNT_PRESETS } from '../../lib/dramaImageStyles'
import { validateProjectCreate, type ProjectCreateConstraints, type ProjectCreateDraft, type ProjectCreateErrors, type ProjectCreateValues } from '../../lib/projectCreateForm'
import './project-create.css'

export type { ProjectCreateValues } from '../../lib/projectCreateForm'

export type ProjectCreateDialogProps = ProjectCreateConstraints & {
  open: boolean
  onClose: () => void
  onSubmit: (values: ProjectCreateValues) => Promise<void>
  contextLabel?: string
  allowImageStyle?: boolean
  submitLabel?: string
  footerNote?: string
}

/** One creation flow for personal projects, team projects, and free canvases. */
export default function ProjectCreateDialog({ open, ...props }: ProjectCreateDialogProps) {
  // Unmount the form on close so a new opening always starts with clean inputs.
  return open ? <ProjectCreateDialogForm key={`${props.kind}:${props.contextLabel || ''}`} {...props} /> : null
}

function ProjectCreateDialogForm({
  onClose,
  onSubmit,
  kind,
  contextLabel = '个人空间',
  titleRequired = false,
  titleMaxLength = 80,
  sourceMaxLength = 2000,
  allowImageStyle = true,
  submitLabel,
  footerNote,
}: Omit<ProjectCreateDialogProps, 'open'>) {
  const formId = useId()
  const canvas = kind === 'canvas'
  const [draft, setDraft] = useState<ProjectCreateDraft>({ title: canvas ? '未命名画布' : '', source: '', description: '', episodeCount: '12', imageStyleId: '' })
  const [errors, setErrors] = useState<ProjectCreateErrors>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const Icon = canvas ? LayoutGrid : Clapperboard
  const title = canvas ? '新建自由画布' : '新建漫剧项目'
  const sourceLength = draft.source.trim().length

  function update<K extends keyof ProjectCreateDraft>(key: K, value: ProjectCreateDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
    if (key in errors) setErrors((current) => ({ ...current, [key]: undefined }))
    setError('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting.current) return
    const result = validateProjectCreate(draft, { kind, titleRequired, titleMaxLength, sourceMaxLength })
    setErrors(result.errors)
    if (Object.keys(result.errors).length) {
      const field = Object.keys(result.errors)[0]
      const input = document.getElementById(`${formId}-${field}`)
      const details = input?.closest('details')
      if (details) details.open = true
      input?.focus()
      return
    }
    submitting.current = true
    setBusy(true)
    setError('')
    try { await onSubmit(result.values) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '创建失败，请重试') }
    finally { submitting.current = false; setBusy(false) }
  }

  const fieldError = (name: keyof ProjectCreateErrors) => errors[name] ? <span className="pf-project-create-field-error" id={`${formId}-${name}-error`} role="alert">{errors[name]}</span> : null

  return <Modal open onClose={onClose} dismissible={!busy} title={title} size="lg" className="pf-project-create-modal" footer={<>
    <p className="pf-project-create-footer-note">{footerNote || (canvas ? '先创建画布，再自由添加图片、视频与灵感。' : '创建后进入项目，可继续完善故事与分集。')}</p>
    <div className="pf-project-create-actions"><Button onClick={onClose} disabled={busy}>取消</Button><Button variant="lime" type="submit" form={formId} disabled={busy}>{busy ? <><LoaderCircle size={16} className="pf-project-create-spinner" />正在创建…</> : <>{submitLabel || (canvas ? '创建画布' : '创建项目')}<ArrowUpRight size={16} /></>}</Button></div>
  </>}>
    <form id={formId} className="pf-project-create-form" onSubmit={submit} noValidate aria-busy={busy}>
      <div className="pf-project-create-intro"><span className="pf-project-create-icon"><Icon size={24} strokeWidth={1.6} /></span><div><span className="pf-project-create-context">{contextLabel}</span><h4>{canvas ? '给灵感一张自由的画布' : '从一个好故事开始'}</h4><p>{canvas ? '把图片、视频和想法放在一起，自由连接与创作。' : '写下人物、冲突和故事走向，让创作有一个清晰的起点。'}</p></div></div>

      <label className="pf-project-create-field" htmlFor={`${formId}-title`}><span className="pf-project-create-label">{canvas ? '画布名称' : '项目名称'}{!canvas && !titleRequired && <small>选填，留空将根据故事命名</small>}</span><input id={`${formId}-title`} autoFocus value={draft.title} disabled={busy} onChange={(event) => update('title', event.target.value)} placeholder={canvas ? '为这张画布取个名字' : '例如：雨夜来信'} maxLength={titleMaxLength} aria-invalid={!!errors.title} aria-describedby={errors.title ? `${formId}-title-error` : undefined} required={canvas || titleRequired} />{fieldError('title')}</label>

      {!canvas && <label className="pf-project-create-field pf-project-create-story" htmlFor={`${formId}-source`}><span className="pf-project-create-label">故事内容<span className="pf-project-create-required">必填</span></span><textarea id={`${formId}-source`} value={draft.source} disabled={busy} onChange={(event) => update('source', event.target.value)} rows={6} placeholder={'主角是谁？他遇到了什么麻烦，又想得到什么？\n\n写下故事创意，或粘贴已有的故事与剧本。'} maxLength={sourceMaxLength} aria-invalid={!!errors.source} aria-describedby={`${formId}-source-hint${errors.source ? ` ${formId}-source-error` : ''}`} required /><span className={`pf-project-create-input-meta${sourceLength >= 20 ? ' is-ready' : ''}`} id={`${formId}-source-hint`}><span>{sourceLength >= 20 ? <><Check size={13} />故事已就绪</> : '至少 20 字，建议包含人物与核心冲突'}</span><span>{sourceLength.toLocaleString()} / {sourceMaxLength.toLocaleString()}</span></span>{fieldError('source')}</label>}

      {!canvas && <div className={`pf-project-create-settings${!allowImageStyle ? ' pf-project-create-settings--single' : ''}`}><fieldset className="pf-project-create-episodes" disabled={busy}><legend>计划集数 <small>后续可调整</small></legend><div className="pf-project-create-episode-presets">{EPISODE_COUNT_PRESETS.map((count) => <button key={count} type="button" aria-pressed={Number(draft.episodeCount) === count} onClick={() => update('episodeCount', String(count))}>{count} 集</button>)}</div><label className="pf-project-create-episode-custom" htmlFor={`${formId}-episodeCount`}><span>自定义</span><input id={`${formId}-episodeCount`} type="number" value={draft.episodeCount} onChange={(event) => update('episodeCount', event.target.value)} min={1} max={120} step={1} inputMode="numeric" aria-invalid={!!errors.episodeCount} aria-describedby={`${formId}-episodeCount-hint${errors.episodeCount ? ` ${formId}-episodeCount-error` : ''}`} required /><span>集</span><small id={`${formId}-episodeCount-hint`}>1–120 集</small></label>{fieldError('episodeCount')}</fieldset>{allowImageStyle && <div className="pf-project-create-style"><span className="pf-project-create-label"><Sparkles size={14} />画面风格<small>选填</small></span><DramaImageStyleModal value={draft.imageStyleId} onChange={(value) => update('imageStyleId', value)} disabled={busy} variant="field" fieldLabel="" emptyLabel="选择画面风格" /><p>为角色和场景设置统一的视觉方向。</p></div>}</div>}

      <details className="pf-project-create-description" open={canvas || undefined}><summary>{canvas ? '画布说明' : '补充项目简介'}<span>选填</span></summary><label className="pf-project-create-field" htmlFor={`${formId}-description`}><span className="pf-project-create-sr-only">{canvas ? '画布说明' : '项目简介'}</span><textarea id={`${formId}-description`} value={draft.description} onChange={(event) => update('description', event.target.value)} rows={3} maxLength={500} disabled={busy} placeholder={canvas ? '这张画布想探索什么？' : '用一两句话概括作品，方便在项目列表中识别。'} aria-invalid={!!errors.description} aria-describedby={errors.description ? `${formId}-description-error` : undefined} /><span className="pf-project-create-input-meta"><span>会显示在项目卡片中</span><span>{draft.description.trim().length} / 500</span></span>{fieldError('description')}</label></details>
      {error && <div className="pf-project-create-error" role="alert">{error}</div>}
    </form>
  </Modal>
}
