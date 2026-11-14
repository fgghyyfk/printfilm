import { translate as translateUi } from '../../i18n/runtime'
import { useI18n } from '../../i18n'
import { readEpisodeJob } from '../../lib/dramaEpisodeJobs'
import { isRequestAborted } from '../../api/http'
import { TeamEditingBoundary } from '../../features/teams/TeamEditingBoundary'
/** 剧情大纲：左栏分集目录 + 右栏本集梗概/剧本与可选创作备注 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  Lightbulb,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Maximize2,
  Pencil,
} from 'lucide-react'
import { dramaApi, resolveDramaMediaUrl, type DramaEpisode, type DramaEpisodeBody, type DramaProject, type DramaScript } from '../../api/drama'
import { FragmentPlanSkillModal } from '../../components/drama/FragmentPlanSkillModal'
import { dialog } from '../../lib/dialog'
import {
  buildOutlineDirectory,
  episodeBodyCharLen,
  isSubstantialEpisodeBody,
  isSubstantialEpisodeCreative,
  mergeDirectoryEpisodeBodies,
  MIN_EPISODE_BODY_CHARS,
  MIN_EPISODE_CREATIVE_CHARS,
  parseEpisodeBodies,
} from './dramaWorkspaceUtils'
import { sumFragmentContentDuration } from './dramaEpisodeEditUtils'
import { OutlineScriptParseModal, OutlineScriptPreview } from './outlineScriptPreview'
import DramaStatusNotice, { DramaErrorNotice } from './DramaStatusNotice'

type SectionKey = 'creative' | 'summary' | 'body'

const GENERATION_TITLES: Record<string, string> = {
  creative: '正在生成本集创作思路',
  summary: '正在生成本集梗概',
  brief: '正在提取本集梗概',
  body: '正在生成拍摄剧本',
  full: '正在重新生成整集',
}

/** 分集目录用的镜头合计文案 */
function formatOutlineShotDuration(sec: number): string {
  if (sec <= 0) return '—'
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return s ? translateUi("workflow.minutesSeconds", { v1: String(m), v2: String(s) }) : translateUi("workflow.wholeMinutes", { v1: String(m) })
}

type OutlineEpisodePanelProps = {
  projectId: number
  script: DramaScript | null
  episodeCount: number
  summaryReady: boolean
  /** 全剧摘要生成中（外部目录/空态需显示，不依赖弹窗） */
  summaryGenerating?: boolean
  /** 整剧摘要后抽取资产中 */
  assetsSeeding?: boolean
  episodeGenerating: boolean
  /** 整剧分集剧本进度 */
  episodeProgress?: { done: number; total: number }
  imageStyleLabel?: string
  storyType?: string
  onScriptChange: (script: DramaScript) => void
  onProjectChange: (project: DramaProject) => void
  onError: (msg: string) => void
  onOpenEpisodes: () => void
  children?: (parts: { directory: ReactNode; bodies: ReactNode }) => ReactNode
}

type SectionCardProps = {
  sectionKey: SectionKey
  icon: ReactNode
  title: string
  subtitle: string
  text: string
  editing: boolean
  draft: string
  open: boolean
  busy: boolean
  /** 禁用「生成」按钮；其他集生成中时仍可编辑本集 */
  generateBusy?: boolean
  placeholder: string
  regenerateLabel: string
  onToggle: () => void
  onEdit: () => void
  onCancel: () => void
  onSave: () => void
  onDraftChange: (v: string) => void
  onRegenerate?: () => void
  onCopy: () => void
  /** 剧本区：解析预览 + 分段编辑 */
  scriptPreview?: ReactNode
}

// 分区卡片：图标标题 + 编辑/生成/复制/折叠
function SectionCard({
  sectionKey,
  icon,
  title,
  subtitle,
  text,
  editing,
  draft,
  open,
  busy,
  generateBusy,
  placeholder,
  regenerateLabel,
  onToggle,
  onEdit,
  onCancel,
  onSave,
  onDraftChange,
  onRegenerate,
  onCopy,
  scriptPreview,
}: SectionCardProps) {
  const { t: translateUi } = useI18n()
  const genBusy = generateBusy ?? busy
  return (
    <article className={`drama-outline-section${open ? ' is-open' : ''}`}>
      <header className="drama-outline-section-head">
        <div className="drama-outline-section-title">
          <span className={`drama-outline-section-icon is-${sectionKey}`} aria-hidden>
            {icon}
          </span>
          <div>
            <strong>{title}</strong>
            <p>{subtitle}</p>
          </div>
        </div>
        <div className="drama-outline-section-actions">
          <button type="button" className="drama-outline-text-btn" disabled={busy} onClick={onEdit}>
            <Pencil size={14} strokeWidth={2} />
            {translateUi("workflow.edit")}
          </button>
          {onRegenerate ? (
            <button type="button" className="drama-outline-text-btn" disabled={genBusy} onClick={onRegenerate}>
              <RefreshCw size={14} strokeWidth={2} />
              {regenerateLabel}
            </button>
          ) : null}
          <button type="button" className="drama-outline-text-btn" onClick={onCopy}>
            <Copy size={14} strokeWidth={2} />
            {translateUi("workflow.duplicate")}
          </button>
          <button
            type="button"
            className="drama-outline-text-btn drama-outline-text-btn-icon"
            onClick={onToggle}
            aria-label={sectionKey === 'body' ? translateUi("workflow.openParsedScript") : open ? translateUi("compliance.collapse") : translateUi("compliance.expand")}
          >
            {sectionKey === 'body' ? (
              <Maximize2 size={14} strokeWidth={2} />
            ) : open ? (
              <ChevronUp size={14} strokeWidth={2} />
            ) : (
              <ChevronDown size={14} strokeWidth={2} />
            )}
            {sectionKey === 'body' ? translateUi("workflow.parsedPreview") : open ? translateUi("compliance.collapse") : translateUi("compliance.expand")}
          </button>
        </div>
      </header>
      {open ? (
        <div className="drama-outline-section-body">
          {editing ? (
            <>
              <textarea
                className="drama-ep-section-textarea"
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                rows={sectionKey === 'body' ? 14 : 8}
                placeholder={placeholder}
                disabled={busy}
              />
              <div className="drama-ep-section-edit-actions">
                <button type="button" className="drama-btn-ghost" disabled={busy} onClick={onCancel}>
                  {translateUi("ecomSet.copyModal.cancel")}
                </button>
                <button type="button" className="drama-btn-primary" disabled={busy} onClick={onSave}>
                  {translateUi("workflow.save")}
                </button>
              </div>
            </>
          ) : scriptPreview ? (
            scriptPreview
          ) : (
            <p className="drama-pre drama-outline-section-text">{text.trim() || placeholder}</p>
          )}
        </div>
      ) : null}
    </article>
  )
}
// 分集目录 + 本集梗概、拍摄剧本、可选备注
export function OutlineEpisodePanel({
  projectId,
  script,
  episodeCount,
  summaryReady,
  summaryGenerating = false,
  assetsSeeding = false,
  episodeGenerating,
  episodeProgress = { done: 0, total: 0 },
  imageStyleLabel,
  storyType,
  onScriptChange,
  onProjectChange,
  onError,
  onOpenEpisodes,
  children,
}: OutlineEpisodePanelProps) {
  const { t: translateUi } = useI18n()
  const navigate = useNavigate()
  const [activeEpisodeNumber, setActiveEpisodeNumber] = useState(1)
  const [openSections, setOpenSections] = useState<Set<SectionKey>>(
    () => new Set(['summary', 'body']),
  )
  const [editingSection, setEditingSection] = useState<SectionKey | null>(null)
  const [sectionDraft, setSectionDraft] = useState('')
  const [titleDraft, setTitleDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [localJobs, setLocalJobs] = useState<Record<number, string>>({})
  const [confirming, setConfirming] = useState(false)
  const [enterSkillOpen, setEnterSkillOpen] = useState(false)
  const [localError, setLocalError] = useState('')
  const [localNotice, setLocalNotice] = useState('')
  const [scriptModalOpen, setScriptModalOpen] = useState(false)
  const [sceneEditing, setSceneEditing] = useState(false)
  const [episodeCovers, setEpisodeCovers] = useState<Record<number, string>>({})
  const [episodeShotStats, setEpisodeShotStats] = useState<
    Record<number, { episodeId: number; fragmentCount: number; totalSec: number; planStatus: string; scriptChanged: boolean }>
  >({})

  const episodeBodies = parseEpisodeBodies(script)
  const directoryEpisodes = buildOutlineDirectory(episodeBodies, episodeCount)
  const displayEpisodes = mergeDirectoryEpisodeBodies(directoryEpisodes, episodeBodies)
  const selected =
    displayEpisodes.find((ep) => ep.episodeNumber === activeEpisodeNumber) || displayEpisodes[0] || null

  const selectedJob = readEpisodeJob(script, selected?.episodeNumber || 0)
  const generatingMode = selected ? localJobs[selected.episodeNumber || 0] || selectedJob.mode || null : null
  const generatingEpisodeNumber = Number(Object.keys(localJobs).find((number) => Number(number) !== selected?.episodeNumber) || 0) || null

  useEffect(() => {
    if (!selected) return
    if (!displayEpisodes.some((ep) => ep.episodeNumber === activeEpisodeNumber) && displayEpisodes[0]) {
      setActiveEpisodeNumber(displayEpisodes[0].episodeNumber || 1)
    }
  }, [displayEpisodes, activeEpisodeNumber, selected])

  useEffect(() => {
    setEditingSection(null)
    setSectionDraft('')
    setTitleDraft(selected?.title || '')
    setLocalError('')
    setLocalNotice('')
    setScriptModalOpen(false)
    setSceneEditing(false)
  }, [selected?.episodeNumber])

  useEffect(() => {
    if (!editingSection) setTitleDraft(selected?.title || '')
  }, [selected?.title, editingSection])

  // 读取分集 AI 分镜状态（含自动入队）
  function readPlanStatus(ep: DramaEpisode): string {
    const active = (ep.active_tasks || []).find((task) => task.task_type === 'fragment_plan')
    if (
      active &&
      !active.cancel_requested &&
      ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(active.status)
    ) {
      return 'generating'
    }
    const st = ep.params?.fragment_plan_status
    return typeof st === 'string' ? st : ''
  }

  // 从分集列表刷新封面 / 镜数 / 分镜状态
  function applyEpisodeRows(rows: DramaEpisode[]) {
    const map: Record<number, string> = {}
    const shotMap: Record<number, { episodeId: number; fragmentCount: number; totalSec: number; planStatus: string; scriptChanged: boolean }> =
      {}
    for (const ep of rows) {
      const epNo = Number(ep.params?.episodeNumber) || 0
      if (!epNo) continue
      const frags = [...(ep.fragments || [])].sort(
        (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0),
      )
      const planStatus = readPlanStatus(ep)
      const totalSec =
        frags.length > 0
          ? frags.reduce((sum, f) => {
              const stored = Number(f.duration_sec)
              if (Number.isFinite(stored) && stored > 0) return sum + Math.round(stored)
              return sum + sumFragmentContentDuration(f.content || '')
            }, 0)
          : 0
      shotMap[epNo] = {
        episodeId: ep.id,
        fragmentCount: frags.length,
        totalSec,
        planStatus,
        scriptChanged: ep.params?.fragment_script_changed === true,
      }
      const first = frags.find((f) => (f.cover || '').trim() || (f.video || '').trim())
      if (!first) continue
      const raw = (first.cover || first.video || '').trim()
      if (raw) map[epNo] = resolveDramaMediaUrl(raw)
    }
    setEpisodeCovers(map)
    setEpisodeShotStats(shotMap)
  }

  // 只读已有分集行；勿提前规则 seed（否则会假显示「1 镜」）。分镜中持续轮询。
  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function refresh() {
      try {
        const rows: DramaEpisode[] = await dramaApi.listEpisodes(projectId)
        if (cancelled) return
        applyEpisodeRows(rows)
        const anyPlanning = rows.some((ep) => readPlanStatus(ep) === 'generating')
        // 分集剧本还在跑时也继续刷，以便每集完成后立刻看到「AI 分镜中」
        if (anyPlanning || episodeGenerating) {
          timer = window.setTimeout(() => {
            void refresh()
          }, 2500)
        }
      } catch {
        if (!cancelled) {
          setEpisodeCovers({})
          setEpisodeShotStats({})
        }
      }
    }

    void refresh()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [projectId, script?.id, script?.episode_content, episodeCount, episodeGenerating, generatingMode])

  const selectedGenerating = Boolean(selected && localJobs[selected.episodeNumber || 0]) || ['queued', 'generating', 'running'].includes(selectedJob.status)
  const anyEpisodeGenerating = Object.keys(localJobs).length > 0
  const effectiveCreative =
    editingSection === 'creative' ? sectionDraft : selected?.creative || ''
  const effectiveSummary =
    editingSection === 'summary' ? sectionDraft : selected?.summary || ''
  const canGenerateBody =
    episodeBodyCharLen(effectiveCreative) >= 10 || episodeBodyCharLen(effectiveSummary) >= 40
  const canRegenerateEpisode =
    isSubstantialEpisodeCreative(effectiveCreative) || episodeBodyCharLen(effectiveSummary) >= 40
  const canAdd =
    summaryReady &&
    !episodeGenerating &&
    !adding &&
    !anyEpisodeGenerating &&
    !confirming &&
    directoryEpisodes.length < 120

  // 整剧批量生成时：已写完正文的集可编辑 / 进分镜 / 重生成；未完成集仍锁生成
  // 单集任务按集号独立；只禁用当前正在生成的一集
  const selectedBodyReady = isSubstantialEpisodeBody(selected?.body)
  const summaryGenerateMode = selectedBodyReady
    ? 'brief'
    : isSubstantialEpisodeCreative(selected?.creative) ? 'summary' : null
  const batchLocksThisEpisode = episodeGenerating && !selectedBodyReady
  const busy = selectedGenerating || confirming || saving || adding
  const generateBusy =
    selectedGenerating || batchLocksThisEpisode || confirming || saving || adding
  const enterBusy = busy || !selectedBodyReady

  async function pollOptimize(tokenEpisode: number) {
    const started = Date.now()
    let lastErr: Error | null = null
    while (Date.now() - started < 8 * 60 * 1000) {
      await new Promise((r) => setTimeout(r, 2000))
      let cur: DramaScript
      try { cur = await dramaApi.getScript(projectId) }
      catch (err) { if (isRequestAborted(err)) throw err; lastErr = err instanceof Error ? err : new Error(translateUi("workflow.couldNotRefreshProgress")); continue }
      onScriptChange(cur)
      lastErr = null
      const job = readEpisodeJob(cur, tokenEpisode)
      if (job.status === 'completed') return cur
      if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error || '本集生成失败或已取消')

    }
    throw lastErr || new Error(translateUi("workflow.generationTimedOutRefreshLaterToCheckItsStatus"))
  }

  async function saveBodies(nextBodies: DramaEpisodeBody[]) {
    if (!selected?.episodeNumber) throw new Error('请先选择分集')
    const current = nextBodies.find((item) => item.episodeNumber === selected.episodeNumber)
    if (!current) throw new Error('分集不存在，请刷新')
    await dramaApi.updateScriptEpisode(projectId, selected.episodeNumber, { title: current.title, creative: current.creative, summary: current.summary, body: current.body, version: Number((script?.params?.episode_versions as Record<string, number> | undefined)?.[String(selected.episodeNumber)] ?? 0) })
    const updated = await dramaApi.getScript(projectId)
    onScriptChange(updated)
    return updated
  }

  async function handleAddEpisode() {
    if (!canAdd) return
    setAdding(true)
    setLocalError('')
    try {
      const res = await dramaApi.addEpisode({ project_id: projectId })
      onScriptChange(res.script)
      const p = await dramaApi.getProject(projectId)
      onProjectChange(p)
      setActiveEpisodeNumber(res.episode_number)
      setEditingSection('summary')
      setSectionDraft('')
      setOpenSections((prev) => new Set(prev).add('summary'))
      onOpenEpisodes()
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotAddTheEpisode")
      setLocalError(msg)
      onError(msg)
    } finally {
      setAdding(false)
    }
  }

  async function handleSaveSection(section: SectionKey) {
    if (!selected?.episodeNumber) return
    setSaving(true)
    setLocalError('')
    try {
      const num = selected.episodeNumber
      const next = displayEpisodes.map((ep) => {
        if (ep.episodeNumber !== num) return ep
        if (section === 'creative') return { ...ep, creative: sectionDraft, title: titleDraft || ep.title }
        if (section === 'summary') return { ...ep, summary: sectionDraft, title: titleDraft || ep.title }
        return { ...ep, body: sectionDraft, title: titleDraft || ep.title }
      })
      await saveBodies(next)
      setEditingSection(null)
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotSave")
      setLocalError(msg)
      onError(msg)
    } finally {
      setSaving(false)
    }
  }

  async function handleGenerate(mode: 'summary' | 'body' | 'full' | 'brief') {
    if (!selected?.episodeNumber) return
    if (editingSection || sceneEditing) {
      setLocalError(translateUi("workflow.saveOrCancelYourCurrentEditsBeforeGenerating"))
      return
    }
    const creative = selected.creative || ''
    const summary = selected.summary || ''
    if (mode === 'summary' && !isSubstantialEpisodeCreative(creative)) {
      setLocalError(translateUi("workflow.creativeNotesMinimum", { v1: String(MIN_EPISODE_CREATIVE_CHARS) }))
      return
    }
    if (mode === 'full' && !isSubstantialEpisodeCreative(creative) && episodeBodyCharLen(summary) < 40) {
      setLocalError(translateUi("workflow.synopsisOrNotesMinimum", { v1: String(MIN_EPISODE_CREATIVE_CHARS) }))
      return
    }
    // 与后端 run_episode_body_from_brief 一致：创意≥10 或摘要≥40
    if (mode === 'body' && episodeBodyCharLen(creative) < 10 && episodeBodyCharLen(summary) < 40) {
      setLocalError(translateUi("workflow.enterThisEpisodeSSynopsisBeforeGeneratingItsShooting"))
      return
    }
    if (mode === 'brief' && !isSubstantialEpisodeBody(selected.body)) {
      setLocalError(translateUi("workflow.scriptMinimumForSynopsis", { v1: String(MIN_EPISODE_BODY_CHARS) }))
      return
    }
    if (mode === 'full') {
      const ok = await dialog.confirm({
        title: translateUi("workflow.regenerateEpisode"),
        message:
          translateUi("workflow.rewriteTheShootingScriptFromTheEpisodeSynopsisOr"),
        confirmText: translateUi("ecomSet.result.regenerate"),
        tone: 'danger',
      })
      if (!ok) return
    }
    const targetEpisode = selected.episodeNumber
    setLocalJobs((jobs) => ({ ...jobs, [targetEpisode]: mode }))
    setLocalError('')
    setLocalNotice('')
    try {
      await dramaApi.episodeScript({
        project_id: projectId,
        episode_number: targetEpisode,
        generate_mode: mode,
        creative: creative || undefined,
        title: selected.title,
      })
      const cur = await pollOptimize(targetEpisode)
      const created = readEpisodeJob(cur, targetEpisode).assetsCreated
      if ((mode === 'body' || mode === 'full') && created > 0) {
        setLocalNotice(translateUi("workflow.createdCharacterSceneCount", { v1: String(created) }))
      }
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("ecomSet.result.failed")
      setLocalError(msg)
      onError(msg)
    } finally {
      setLocalJobs((jobs) => { const next = { ...jobs }; delete next[targetEpisode]; return next })
    }
  }

  async function handleConfirmEnter() {
    if (!selected?.episodeNumber) return
    if (editingSection || sceneEditing) {
      setLocalError(translateUi("workflow.saveOrCancelYourCurrentEditsBeforeOpeningThe"))
      return
    }
    if (!isSubstantialEpisodeBody(selected.body)) {
      setLocalError(translateUi("workflow.scriptMinimumForStoryboard", { v1: String(MIN_EPISODE_BODY_CHARS) }))
      return
    }
    setLocalError('')
    setConfirming(true)
    try {
      // 已自动入队/完成 AI 分镜：直接进入编辑页（页内会轮询「AI 分镜中」）
      const rows = await dramaApi.listEpisodes(projectId)
      const existing = rows.find(
        (ep) => Number(ep.params?.episodeNumber) === Number(selected.episodeNumber),
      )
      const planSt = existing ? readPlanStatus(existing) : ''
      if (
        existing &&
        (planSt === 'generating' ||
          planSt === 'completed' ||
          (existing.fragments || []).length > 0)
      ) {
        navigate(`/drama/projects/${projectId}/episodes/${existing.id}`)
        return
      }
      setEnterSkillOpen(true)
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotOpenTheStoryboard")
      setLocalError(msg)
      onError(msg)
    } finally {
      setConfirming(false)
    }
  }

  // 首次进入：确认剧本 + 按所选 Skill 做 AI 分镜
  async function startEnterWithSkills(skillIds: number[]) {
    if (!selected?.episodeNumber) return
    setEnterSkillOpen(false)
    setConfirming(true)
    setLocalError('')
    try {
      const res = await dramaApi.confirmEpisodeFromScript({
        project_id: projectId,
        episode_number: selected.episodeNumber,
      })
      const episodeId = res.episode.id
      try {
        await dramaApi.planEpisodeFragments(episodeId, {
          force: true,
          fallback_rules: true,
          skill_ids: skillIds,
        })
      } catch (planErr) {
        if (isRequestAborted(planErr)) return
        onError(planErr instanceof Error ? planErr.message : translateUi("workflow.aiStoryboardingCouldNotBeQueuedRuleBasedStoryboarding"))
      }
      navigate(`/drama/projects/${projectId}/episodes/${episodeId}`)
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotOpenTheStoryboard")
      setLocalError(msg)
      onError(msg)
    } finally {
      setConfirming(false)
    }
  }

  async function handleSaveScenes(nextBody: string) {
    if (!selected?.episodeNumber) return
    setSaving(true)
    setLocalError('')
    try {
      const num = selected.episodeNumber
      const next = displayEpisodes.map((ep) =>
        ep.episodeNumber === num ? { ...ep, body: nextBody, title: titleDraft || ep.title } : ep,
      )
      await saveBodies(next)
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotSave")
      setLocalError(msg)
      onError(msg)
      throw err
    } finally {
      setSaving(false)
    }
  }

  function toggleSection(key: SectionKey) {
    if (key === 'body') {
      setScriptModalOpen(true)
      setOpenSections((prev) => new Set(prev).add('body'))
      return
    }
    setOpenSections((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function startEdit(section: SectionKey) {
    if (!selected) return
    if (sceneEditing) {
      setLocalError(translateUi("workflow.saveOrCancelTheSceneYouAreEditingFirst"))
      return
    }
    setEditingSection(section)
    setSectionDraft(
      section === 'creative'
        ? selected.creative || ''
        : section === 'summary'
          ? selected.summary || ''
          : selected.body || '',
    )
    setOpenSections((prev) => new Set(prev).add(section))
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text || '')
    } catch {
      setLocalError(translateUi("workflow.couldNotDuplicate"))
    }
  }

  const metaTags = useMemo(() => {
    const tags: string[] = []
    if (storyType) tags.push(storyType)
    if (selected?.origin === 'manual') tags.push(translateUi("workflow.manuallyAddedEpisode"))
    else if (selected?.body) tags.push(translateUi("workflow.automaticallyGenerated"))
    return tags
  }, [storyType, selected])

  const bodyReady = selectedBodyReady
  const selectedShot = episodeShotStats[selected?.episodeNumber || 0]
  const charHint = selected
    ? [
        selected.summary ? translateUi("workflow.synopsisCharCount", { v1: String(episodeBodyCharLen(selected.summary)) }) : null,
        selected.body ? translateUi("workflow.scriptCharCount", { v1: String(episodeBodyCharLen(selected.body)) }) : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : ''

  const directory = (
    <aside className="drama-outline-sidebar">
      <div className="drama-outline-sidebar-head">
        <div>
          <h3>{translateUi("workflow.episodes_4c659c")}</h3>
          <p>{translateUi("workflow.episodes_5b28c7", { v1: directoryEpisodes.length })}</p>
        </div>
        {summaryReady ? (
          <button
            type="button"
            className="drama-outline-add-btn"
            disabled={!canAdd}
            onClick={() => void handleAddEpisode()}
          >
            <Plus size={14} strokeWidth={2.5} />
            {adding ? translateUi("workflow.adding") : translateUi("workflow.addEpisode")}
          </button>
        ) : null}
      </div>
      <ul className="drama-outline-ep-list">
        {directoryEpisodes.map((ep) => {
          const body = displayEpisodes.find((x) => x.episodeNumber === ep.episodeNumber)
          const ready = isSubstantialEpisodeBody(body?.body)
          const active = activeEpisodeNumber === ep.episodeNumber
          const shot = episodeShotStats[ep.episodeNumber || 0]
          const statusLabel = summaryGenerating
            ? translateUi("workflow.generatingDramaSummary")
            : assetsSeeding
              ? translateUi("workflow.extractingAssets")
              : shot?.planStatus === 'generating'
                ? translateUi("workflow.planningAiStoryboards")
                : shot?.planStatus === 'failed'
                  ? translateUi("workflow.storyboardPlanningFailed")
                  : shot?.scriptChanged && shot.fragmentCount > 0
                    ? translateUi("workflow.shotsOlderScript", { v1: String(shot.fragmentCount), v2: String(formatOutlineShotDuration(shot.totalSec)) })
                    : shot?.planStatus === 'completed' &&
                      shot.fragmentCount > 0 &&
                      shot.totalSec > 0
                    ? shot.fragmentCount <= 1 && ready
                      ? translateUi("workflow.text1ShotFewShots", { v1: String(formatOutlineShotDuration(shot.totalSec)) })
                      : translateUi("workflow.shots", { v1: String(shot.fragmentCount), v2: String(formatOutlineShotDuration(shot.totalSec)) })
                    : episodeGenerating && !ready && !body?.summary && !body?.creative
                      ? episodeProgress.total > 0
                        ? translateUi("workflow.dramaProgress", { v1: String(episodeProgress.done), v2: String(episodeProgress.total) })
                        : translateUi("workflow.waitingForScriptGeneration")
                      : ready
                        ? translateUi("workflow.aiStoryboardsPending")
                        : body?.summary || body?.creative
                          ? translateUi("workflow.scriptPending")
                          : translateUi("workflow.synopsisNeeded")
          return (
            <li key={ep.episodeNumber}>
              <button
                type="button"
                className={`drama-outline-ep-card${active ? ' is-active' : ''}`}
                onClick={() => {
                  onOpenEpisodes()
                  setActiveEpisodeNumber(ep.episodeNumber)
                }}
              >
                <span className="drama-outline-ep-thumb" aria-hidden>
                  {(() => {
                    const cover = episodeCovers[ep.episodeNumber || 0]
                    if (!cover) return ep.episodeNumber
                    if (/\.(mp4|webm|mov)(\?|$)/i.test(cover)) {
                      return <video src={cover} muted playsInline preload="metadata" />
                    }
                    return <img src={cover} alt="" />
                  })()}
                </span>
                <span className="drama-outline-ep-meta">
                  <strong>{translateUi("workflow.episode_7518ae", { v1: ep.episodeNumber })}</strong>
                  <small>{ep.title || translateUi("workflow.untitled")}</small>
                  <em
                    className={
                      summaryGenerating ||
                      assetsSeeding ||
                      shot?.planStatus === 'generating' ||
                      (episodeGenerating && !ready) ||
                      (ready && shot?.planStatus !== 'completed')
                        ? 'is-waiting'
                        : shot?.planStatus === 'completed' && shot.fragmentCount > 0
                          ? 'is-shot'
                          : undefined
                    }
                  >
                    {statusLabel}
                  </em>
                </span>
                <span className="drama-outline-ep-more" aria-hidden>
                  <MoreHorizontal size={16} />
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )

  const bodies = !selected ? (
    <div className="drama-outline-detail-empty">
      <p>
        {summaryGenerating
          ? translateUi("workflow.generatingTheDramaSummaryThenExtractingAssetsAndWriting")
          : assetsSeeding
            ? translateUi("workflow.extractingAssetsThenWritingEpisodeScripts")
            : summaryReady
              ? translateUi("workflow.noEpisodesYetChooseAddEpisodeOrWaitFor")
              : translateUi("workflow.completeTheDramaConceptAndScriptSummaryFirst")}
      </p>
    </div>
  ) : (
    <section className="drama-outline-detail">
      <header className="drama-outline-detail-head">
        <div className="drama-outline-detail-title">
          {editingSection ? (
            <input
              className="drama-title-input"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              placeholder={translateUi("workflow.episodeName")}
            />
          ) : (
            <h2>
              {translateUi("workflow.episode_28c99a", { v1: selected.episodeNumber ?? '', v2: selected.title ? `：${selected.title}` : '' })}</h2>
          )}
          {imageStyleLabel ? <span className="drama-outline-style-badge">{imageStyleLabel}</span> : null}
          <div className="drama-outline-detail-meta">
            {charHint ? <span>{charHint}</span> : <span>{translateUi("workflow.noEpisodeContentYet")}</span>}
            {metaTags.map((tag) => (
              <span key={tag} className="drama-outline-tag">
                {tag}
              </span>
            ))}
          </div>
        </div>
        <div className="drama-outline-detail-actions">
          <span className={`drama-outline-saved${bodyReady && !editingSection && !sceneEditing && !selectedShot?.scriptChanged ? ' is-ready' : ''}`}>
            <Check size={14} strokeWidth={2.5} />
            {editingSection || sceneEditing
              ? translateUi("workflow.unsavedEdits")
              : selectedShot?.scriptChanged
                ? translateUi("workflow.storyboardNeedsUpdating")
                : bodyReady
                  ? translateUi("workflow.readyForStoryboards")
                  : saving ? translateUi("workflow.saving_a032e8") : translateUi("workflow.synced")}
          </span>
          {bodyReady && episodeBodyCharLen(selected.summary) < 40 ? (
            <button
              type="button"
              className="drama-btn-ghost"
              disabled={generateBusy}
              onClick={() => void handleGenerate('brief')}
            >
              {selectedGenerating && generatingMode === 'brief' ? translateUi("workflow.extracting") : translateUi("workflow.extractEpisodeSynopsis")}
            </button>
          ) : null}
          <button
            type="button"
            className="drama-outline-regen-btn"
            disabled={generateBusy || !canRegenerateEpisode}
            onClick={() => void handleGenerate('full')}
            title={
              batchLocksThisEpisode
                ? translateUi("workflow.thisEpisodeSScriptIsStillGeneratingPleaseWait")
                : anyEpisodeGenerating
                  ? translateUi("workflow.anotherEpisodeIsGenerating")
                  : undefined
            }
          >
            <RefreshCw size={15} strokeWidth={2.25} />
            {selectedGenerating && generatingMode === 'full' ? translateUi("workflow.generating") : translateUi("workflow.regenerateEpisode")}
          </button>
          {selectedShot?.episodeId && !editingSection && !sceneEditing ? (
            <Link className="drama-btn-primary" to={`/drama/projects/${projectId}/episodes/${selectedShot.episodeId}`}>
              进入分镜
            </Link>
          ) : (
          <button
            type="button"
            className="drama-btn-primary"
            disabled={enterBusy}
            onClick={() => void handleConfirmEnter()}
            title={!selectedBodyReady ? translateUi("workflow.theScriptNeedsAtLeastCharacters", { v1: String(MIN_EPISODE_BODY_CHARS) }) : undefined}
          >
            {confirming ? translateUi("workflow.opening") : translateUi("workflow.openStoryboards")}
          </button>
          )}
        </div>
      </header>

      <div className="drama-outline-status-list">
        {localError ? <DramaErrorNotice message={localError} onDismiss={() => setLocalError('')} /> : null}
        {localNotice ? <DramaStatusNotice tone="success" title={localNotice} onDismiss={() => setLocalNotice('')} /> : null}
        {selectedShot?.scriptChanged ? (
          <DramaStatusNotice tone="warning" title="剧本已更新，分镜待同步">
            现有分镜仍为旧稿，进入分镜后请重新分镜。
          </DramaStatusNotice>
        ) : null}
        {summaryGenerating ? (
          <DramaStatusNotice tone="busy" title="正在生成全剧摘要">
            完成后将抽取角色与场景资产，再生成各集剧本。
          </DramaStatusNotice>
        ) : null}
        {assetsSeeding ? (
          <DramaStatusNotice tone="busy" title="正在抽取角色与场景资产">
            写入资产库后，将开始生成分集剧本。
          </DramaStatusNotice>
        ) : null}
        {episodeGenerating ? (
          <DramaStatusNotice tone="busy" title="正在生成分集剧本">
            {episodeProgress.total > 0
              ? `已完成 ${episodeProgress.done} / ${episodeProgress.total} 集，可先浏览已完成的内容。`
              : '可先浏览已完成的分集内容。'}
          </DramaStatusNotice>
        ) : null}
        {selectedGenerating ? (
          <DramaStatusNotice tone="busy" title={GENERATION_TITLES[generatingMode || ''] || '正在生成本集内容'}>
            内容生成后将自动更新，请稍候。
          </DramaStatusNotice>
        ) : anyEpisodeGenerating && generatingEpisodeNumber ? (
          <DramaStatusNotice tone="busy" title={`第 ${generatingEpisodeNumber} 集正在生成`}>
            可继续创作当前分集。
          </DramaStatusNotice>
        ) : null}
        {selected && episodeShotStats[selected.episodeNumber || 0]?.planStatus === 'generating' ? (
          <DramaStatusNotice tone="busy" title="正在生成本集 AI 分镜">
            分镜完成后，可进入查看与编辑。
          </DramaStatusNotice>
        ) : null}
        {selected &&
        isSubstantialEpisodeBody(selected.body) &&
        !episodeShotStats[selected.episodeNumber || 0]?.planStatus &&
        !selectedGenerating &&
        !episodeGenerating &&
        !summaryGenerating &&
        !assetsSeeding ? (
          <DramaStatusNotice tone="busy" title="本集剧本已就绪，正在准备 AI 分镜" />
        ) : null}
      </div>

      <div className="drama-outline-sections">
        <SectionCard
          sectionKey="summary"
          icon={<BookOpen size={18} strokeWidth={1.9} />}
          title={translateUi("workflow.episodeSynopsis")}
          subtitle={translateUi("workflow.charactersConflictTurnsAndEndingForThisEpisodeThe")}
          text={selected.summary || ''}
          editing={editingSection === 'summary'}
          draft={sectionDraft}
          open={openSections.has('summary')}
          busy={busy}
          generateBusy={generateBusy}
          placeholder={translateUi("workflow.enterAnEpisodeSynopsisAtLeast40CharactersThen")}
          regenerateLabel={
            selectedGenerating && generatingMode === 'brief'
              ? translateUi("workflow.extracting")
              : selectedGenerating && generatingMode === 'summary'
                ? translateUi("workflow.generating")
                : summaryGenerateMode === 'brief'
                  ? translateUi("workflow.extractFromScript")
                  : translateUi("workflow.generateFromNotes")
          }
          onToggle={() => toggleSection('summary')}
          onEdit={() => startEdit('summary')}
          onCancel={() => setEditingSection(null)}
          onSave={() => void handleSaveSection('summary')}
          onDraftChange={setSectionDraft}
          onRegenerate={summaryGenerateMode ? () => void handleGenerate(summaryGenerateMode) : undefined}
          onCopy={() => void copyText(selected.summary || '')}
        />
        <SectionCard
          sectionKey="body"
          icon={<FileText size={18} strokeWidth={1.9} />}
          title={translateUi("workflow.shootingScript")}
          subtitle={translateUi("workflow.shootingScriptWithDialogueAndVisualsParsedIntoEditable")}
          text={selected.body || ''}
          editing={editingSection === 'body'}
          draft={sectionDraft}
          open={openSections.has('body')}
          busy={busy}
          generateBusy={generateBusy || !canGenerateBody}
          placeholder={translateUi("workflow.shootingScriptAtLeastCharactersToOpenStoryboards", { v1: String(MIN_EPISODE_BODY_CHARS) })}
          regenerateLabel={selectedGenerating && generatingMode === 'body' ? translateUi("workflow.generating") : translateUi("workflow.generateScript")}
          onToggle={() => toggleSection('body')}
          onEdit={() => startEdit('body')}
          onCancel={() => setEditingSection(null)}
          onSave={() => void handleSaveSection('body')}
          onDraftChange={setSectionDraft}
          onRegenerate={() => void handleGenerate('body')}
          onCopy={() => void copyText(selected.body || '')}
          scriptPreview={
            <OutlineScriptPreview
              key={selected.episodeNumber}
              text={selected.body || ''}
              empty={translateUi("workflow.shootingScriptAtLeastCharactersToOpenStoryboards", { v1: String(MIN_EPISODE_BODY_CHARS) })}
              busy={busy}
              shotStats={episodeShotStats[selected.episodeNumber || 0] || null}
              onSaveScenes={handleSaveScenes}
              onEditingChange={setSceneEditing}
            />
          }
        />
        <SectionCard
          sectionKey="creative"
          icon={<Lightbulb size={18} strokeWidth={1.9} />}
          title={translateUi("workflow.creativeNotesOptional")}
          subtitle={translateUi("workflow.addCreativeDirectionOrKeepEarlyIdeasOptionalWhen")}
          text={selected.creative || ''}
          editing={editingSection === 'creative'}
          draft={sectionDraft}
          open={openSections.has('creative')}
          busy={busy}
          placeholder={translateUi("workflow.optionalCharacterMotivationsCreativeDirectionOrSpecialRequirements")}
          regenerateLabel={translateUi("workflow.generateSynopsis")}
          onToggle={() => toggleSection('creative')}
          onEdit={() => startEdit('creative')}
          onCancel={() => setEditingSection(null)}
          onSave={() => void handleSaveSection('creative')}
          onDraftChange={setSectionDraft}
          onCopy={() => void copyText(selected.creative || '')}
        />
      </div>
      <OutlineScriptParseModal
        open={scriptModalOpen}
        onClose={() => setScriptModalOpen(false)}
        title={translateUi("workflow.episodeParsedScript", { v1: String(selected.episodeNumber) })}
        text={selected.body || ''}
      />
    </section>
  )

  const editableBodies = <TeamEditingBoundary projectId={projectId} type="episode" resourceId={selected?.episodeNumber || 0} enabled={Boolean(selected)} showReadOnlyNotice={false} allowReadOnlyNavigation onAcquired={async (isCurrent) => { const fresh = await dramaApi.getScript(projectId); if (isCurrent()) onScriptChange(fresh) }}>{bodies}</TeamEditingBoundary>

  if (children) {
    return (
      <>
        {children({ directory, bodies: editableBodies })}
        <FragmentPlanSkillModal
          open={enterSkillOpen}
          title={translateUi("workflow.openStoryboards")}
          message={translateUi("workflow.confirmThisEpisodeSScriptAndSplitItInto")}
          confirmText={confirming ? translateUi("workflow.opening") : translateUi("workflow.startStoryboards")}
          onCancel={() => {
            if (!confirming) setEnterSkillOpen(false)
          }}
          onConfirm={(skillIds) => void startEnterWithSkills(skillIds)}
        />
      </>
    )
  }
  return (
    <div className="drama-outline drama-outline-v2">
      {directory}
      <div className="drama-outline-main">{editableBodies}</div>
      <FragmentPlanSkillModal
        open={enterSkillOpen}
        title={translateUi("workflow.openStoryboards")}
        message={translateUi("workflow.confirmThisEpisodeSScriptAndSplitItInto")}
        confirmText={confirming ? translateUi("workflow.opening") : translateUi("workflow.startStoryboards")}
        onCancel={() => {
          if (!confirming) setEnterSkillOpen(false)
        }}
        onConfirm={(skillIds) => void startEnterWithSkills(skillIds)}
      />
    </div>
  )
}
