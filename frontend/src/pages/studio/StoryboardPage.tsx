import { translate as translateUi } from '../../i18n/runtime'
import { applyComplianceSuggestion } from '../../lib/complianceChanges'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, defaultsFromTemplate } from '../../api'
import type { Project, Shot, Template } from '../../api'
import AppShell from '../../components/layout/AppShell'
import Stepper from '../../components/ui/Stepper'
import ImageTextStoryboard from './ImageTextStoryboard'
import {
  IconChevronLeft,
  IconDownload,
  IconEdit,
  IconImage,
  IconMonitor,
  IconPlay,
  IconRefresh,
  IconSliders,
  IconTrash,
} from '../../components/ui/Icons'
import { scenePromptForDisplay } from '../../promptDisplay'
import { dialog } from '../../lib/dialog'
import { hasActiveUnifiedTasks, requestProjectCancellation } from '../../lib/kepuTaskControls'
import { downloadSingleVideo } from '../../lib/clientDownload'
import { withReturn } from '../../features/marketplace/ui'
import { handleBillingError } from '../../lib/billingError'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ComplianceNotice from '../../components/compliance/ComplianceNotice'
import EcomSetSourceLink from '../../components/daihuo/EcomSetSourceLink'
import { getProjectCompliance, undoProjectReplacement, applyProjectSuggestion } from '../../api/compliance'
import { kepuComplianceLabel, projectComplianceKey, scrollToShotRow, shotRowDomId } from '../../lib/compliance'
import { useI18n } from '../../i18n'
import { getActiveLocale } from '../../i18n/detect'
import { useKepuProgress } from '../../hooks/useKepuProgress'
import {
  effectiveStatus,
  formatMmSs,
  isProjectBusy,
  kepuBillingPhase,
  kepuPhaseHint,
  kepuStepIndex,
  kepuSteps,
  pipelineModeLabel,
  isProjectWideBusy,
  isShotGenerating,
  shotsByNo,
  shotDisplayDone,
  shotDisplayKind,
  shotDisplayLabel,
  statusLabel,
} from '../../lib/status'
import { batchRegenAudioPlan, canRegenShotAudio, shotImageAction } from '../../lib/kepuShotActions'
import {
  SEGMENT_SCRIPT_PLACEHOLDER,
  SHOT_DURATION_MAX,
  firstVisualFromScript,
  narrationFromScript,
  parseSegmentScript,
  replaceFirstVisualInScript,
  replaceNarrationInScript,
  shotDisplayDurationSec,
  sumDuration,
  validateSegmentScriptDuration,
} from '../../lib/segmentDuration'

/** 线上新增文案的中英文切换（未进 i18n 词表的少量句子） */
function lt(zh: string, en: string): string {
  return getActiveLocale() === 'en' ? en : zh
}

function csvEscape(value: string | number | null | undefined) {
  const s = String(value ?? '')
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

function downloadStoryboardCsv(project: Project) {
  const header = [translateUi("workflow.shotNumber"), translateUi("compliance.fields.narration"), translateUi("workflow.visualDescription"), translateUi("workflow.durationSeconds"), translateUi("workflow.status"), translateUi("workflow.shotTitle")]
  const rows = (project.shots || [])
    .slice()
    .sort((a, b) => a.shot_no - b.shot_no)
    .map((s) =>
      [
        s.shot_no,
        s.narration,
        scenePromptForDisplay(s.img_prompt || s.video_prompt || ''),
        shotDisplayDurationSec(s),
        shotDisplayLabel(shotDisplayKind(s, { pipelineMode: project.pipeline_mode })),
        s.overlay_title || '',
      ]
        .map(csvEscape)
        .join(','),
    )
  const bom = '\uFEFF'
  const blob = new Blob([bom + [header.join(','), ...rows].join('\n')], {
    type: 'text/csv;charset=utf-8',
  })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${project.title || `project_${project.id}`}_storyboard.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

type PreviewState =
  | {
      kind: 'shot'
      shotNo: number
      imageUrl: string | null
      videoUrl: string | null
      audioUrl: string | null
      caption: string
    }
  | { kind: 'final'; url: string; title: string; bust?: string }
  | null

function shotCaption(shot: Shot) {
  if (shot.overlay_title) {
    return `${shot.overlay_title}${
      shot.overlay_subtitle ? ` · ${shot.overlay_subtitle}` : ''
    }${shot.narration ? translateUi("workflow.narrationLabelSuffix", { v1: String(shot.narration) }) : ''}`
  }
  return shot.narration
}

export default function StoryboardPage() {
  const { id } = useParams()
  const projectId = Number(id)
  const nav = useNavigate()
  const location = useLocation()
  const { t, t: translateUi } = useI18n()
  const [project, setProject] = useState<Project | null>(null)
  const [template, setTemplate] = useState<Template | null>(null)
  const [busy, setBusy] = useState(false)
  // busyShotIds 正在提交单镜图/视频的镜号，可并行，finally 只删自己
  const [busyShotIds, setBusyShotIds] = useState<Set<number>>(() => new Set())
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<Shot | null>(null)
  const [editFocus, setEditFocus] = useState<string>('')
  /** 表格入口：旁白列 / 逐段分镜列 / 操作栏「编辑」 */
  const [editMode, setEditMode] = useState<'full' | 'narration' | 'segment'>('full')
  const [promptEdit, setPromptEdit] = useState<{
    style_prompt: string
    character_prompt: string
    extra_prompt: string
  } | null>(null)
  const [preview, setPreview] = useState<PreviewState>(null)
  const [menuShotId, setMenuShotId] = useState<number | null>(null)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchSelected, setBatchSelected] = useState<number[]>([])
  const [batchDuration, setBatchDuration] = useState('')
  const [batchRegenAudio, setBatchRegenAudio] = useState(false)
  const coverInputRef = useRef<HTMLInputElement>(null)
  const liveProgress = useKepuProgress(project?.id === projectId ? project : null, setProject)

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      nav('/auth')
      return
    }
    if (!projectId) {
      nav('/studio/new')
      return
    }
    const controller = new AbortController()
    setProject(null)
    setTemplate(null)
    setError('')
    api
      .getProject(projectId, { signal: controller.signal })
      .then((p) => {
        if (controller.signal.aborted) return
        if (p.creation_session_id) {
          nav(`/studio/sessions/${p.creation_session_id}?project=${p.id}`, { replace: true })
          return
        }
        setProject(p)
        return api.templates().then(async (list) => {
          const selected = list.find((t) => t.id === p.template_id)
          const resolved = selected || (p.template_id ? await api.getTemplate(p.template_id) : null)
          if (!controller.signal.aborted) setTemplate(resolved)
        })
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : translateUi("ecomSet.loadFailed"))
      })
    return () => controller.abort()
  }, [nav, projectId])

  useEffect(() => {
    if (menuShotId == null) return
    function onDoc() {
      setMenuShotId(null)
    }
    document.addEventListener('click', onDoc)
    return () => document.removeEventListener('click', onDoc)
  }, [menuShotId])

  // complianceKey 文案签名：保存分镜/重新拆镜后变化即自动重新做广告法检查
  const complianceKey = useMemo(() => projectComplianceKey(project), [project])
  // 等待所有已请求取消的活动任务真正停止；轮询期间保持编辑和生成门禁。
  const cancellationPending = hasActiveUnifiedTasks(project) && Boolean(
    project?.active_tasks?.some((task) => task.cancel_requested || task.status === 'cancel_requested'),
  )
  const running = isProjectBusy(project)
  const step = project ? kepuStepIndex('board', project) : 2
  const totalDuration = useMemo(
    () => (project?.shots || []).reduce((s, x) => s + shotDisplayDurationSec(x), 0),
    [project?.shots],
  )

  // editScriptText 弹窗中当前编辑的逐段脚本
  const editScriptText = editing?.segment_script || editing?.video_prompt || ''
  // editDurationCheck 弹窗脚本时长校验
  const editDurationCheck = useMemo(
    () => validateSegmentScriptDuration(editScriptText),
    [editScriptText],
  )

  const shots = useMemo(() => shotsByNo(project?.shots), [project?.shots])
  /** 项目字段为空时回显模板默认（与后端 _effective_* 一致） */
  const promptDefaults = useMemo(
    () => (template ? defaultsFromTemplate(template) : null),
    [template],
  )
  const displayPrompts = useMemo(() => {
    if (!project) {
      return { style_prompt: '', character_prompt: '', extra_prompt: '' }
    }
    return {
      style_prompt: (project.style_prompt || '').trim() || promptDefaults?.style_prompt || '',
      character_prompt:
        (project.character_prompt || '').trim() || promptDefaults?.character_prompt || '',
      extra_prompt: (project.extra_prompt || '').trim() || promptDefaults?.extra_prompt || '',
    }
  }, [project, promptDefaults])
  const isFullPipeline = project?.pipeline_mode !== 'image_text'
  /**
   * Full pipeline: need AI videos before compose.
   * VIDEO_READY+ means video stage finished (incl. privacy skips without video_url).
   * image_text skips the video stage entirely.
   */
  const phase = project ? kepuBillingPhase(project) : 'script'
  const readyToCompose = phase === 'compose'
  const needsVideos = phase === 'videos'
  const needsScriptConfirm = phase === 'assets'
  const hasFinal = Boolean(project?.final_video_url)
  /** Primary CTA: confirm script → generate → (videos) → compose → preview */
  const primaryAction: 'generate' | 'compose' | 'preview' | 'busy' = running
    ? 'busy'
    : hasFinal
      ? 'preview'
      : readyToCompose
        ? 'compose'
        : 'generate'

  const isAvatar = project?.pipeline_mode === 'avatar'
  const generateLabel = running
    ? translateUi("workflow.generating")
    : shots.length === 0
      ? translateUi("workflow.generateTheStoryboardOnTheStylePage")
      : needsScriptConfirm
        ? isAvatar
          ? translateUi("workflow.confirmStoryboardAndVoiceEachSegment")
          : lt('确认分镜，生成参考图画面', 'Confirm storyboard and generate reference images')
        : needsVideos
          ? isAvatar
            ? translateUi("workflow.generateAvatarVideo")
            : lt('使用图片，逐镜生成视频', 'Generate shot videos from images')
          : translateUi("workflow.continueGeneration")

  // 完整模式的默认旁白由视频模型生成；仅实际存在外部配音时展示 TTS 进度。
  const progressItems = useMemo(() => {
    if (!project) return []
    const list = project.shots || []
    const imgs = list.filter((s) => s.image_url).length
    const auds = list.filter((s) => s.audio_url).length
    const vids = list.filter((s) => s.video_url).length
    const full = project.pipeline_mode !== 'image_text'
    const avatar = project.pipeline_mode === 'avatar'
    const stage = effectiveStatus(project)
    type ProgressItem = { label: string; done: boolean; run?: boolean; pct?: number }
    const items: ProgressItem[] = [
      { label: translateUi("workflow.topicAnalysis"), done: true },
      { label: translateUi("workflow.storyboardScript"), done: list.length > 0 || !['DRAFT', 'SCRIPTING'].includes(project.status) },
      {
        label: avatar ? translateUi("workflow.portraits", { v1: String(imgs), v2: String(list.length || 0) }) : `${lt('参考图画面', 'Reference images')} (${imgs}/${list.length || 0})`,
        done: list.length > 0 && imgs === list.length,
      },
    ]
    if (full) {
      items.push({
        label: `${avatar ? translateUi("workflow.talkingHeadVideo") : lt('镜头视频与声音', 'Shot videos and sound')} (${vids}/${list.length || 0})`,
        done:
          list.length > 0 &&
          (vids === list.length ||
            ['VIDEO_READY', 'COMPOSING', 'AUDITING', 'DONE'].includes(stage)),
        run: stage === 'VIDEOING',
        pct: stage === 'VIDEOING' ? project.progress : undefined,
      })
    }
    if (!full || auds > 0 || stage === 'AUDIOING') {
      items.push({
        label: translateUi("workflow.narration", { v1: String(auds), v2: String(list.length || 0) }),
        done: list.length > 0 && auds === list.length,
        run: stage === 'AUDIOING',
        pct: stage === 'AUDIOING' ? project.progress : undefined,
      })
    }
    items.push({
      label: full ? translateUi("workflow.clipComposition") : translateUi("workflow.finalRendering"),
      done: Boolean(project.final_video_url) || project.status === 'DONE',
      run: stage === 'COMPOSING',
      pct: stage === 'COMPOSING' ? project.progress : undefined,
    })
    return items
  }, [project, translateUi])

  function openFinalPreview() {
    if (!project?.final_video_url) return
    setPreview({
      kind: 'final',
      url: api.assetUrl(project.final_video_url, project.updated_at),
      title: project.title,
      bust: project.updated_at,
    })
  }

  async function continueGenerate() {
    if (!project) return
    setBusy(true)
    setError('')
    try {
      setProject(await api.generate(project.id))
    } catch (err) {
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotContinueGeneration")
      if (msg.includes('合成成片')) {
        try {
          setError('')
          setProject(await api.compose(project.id))
        } catch (e2) {
          setError(e2 instanceof Error ? e2.message : translateUi("workflow.compositionFailed"))
        }
        return
      }
      setError(msg)
      await handleBillingError(err, nav)
    } finally {
      setBusy(false)
    }
  }

  /** 二次确认取消当前项目任务，失败显示原因并保留原来的运行态。 */
  async function cancelGenerate() {
    if (!project) return
    setBusy(true)
    setError('')
    try {
      const result = await requestProjectCancellation(project.id, dialog.confirm, api.cancelProject)
      if (result) setProject(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotCancelGeneration"))
    } finally {
      setBusy(false)
    }
  }

  async function restartGenerate() {
    if (!project) return
    const ok = await dialog.confirm({
      title: translateUi("workflow.startOver"),
      message: translateUi("workflow.replaceTheCurrentStoryboardsAndAssetsWithANew"),
      confirmText: translateUi("workflow.confirmRestart"),
      cancelText: translateUi("workflow.notNow"),
      tone: 'danger',
    })
    if (!ok) return
    setBusy(true)
    setError('')
    try {
      setProject(await api.generate(project.id, { restart: true }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : translateUi("workflow.couldNotRegenerate_dfd1ca")
      setError(msg)
      await handleBillingError(err, nav)
    } finally {
      setBusy(false)
    }
  }

  async function deleteProject() {
    if (!project) return
    const ok = await dialog.confirm({
      title: translateUi("workflow.deleteProject"),
      message: translateUi("workflow.deleteThisProjectAndItsMediaThisCannotBe"),
      confirmText: translateUi("workflow.delete"),
      cancelText: translateUi("ecomSet.copyModal.cancel"),
      tone: 'danger',
    })
    if (!ok) return
    setBusy(true)
    try {
      await api.deleteProject(project.id)
      nav('/history')
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotDelete"))
    } finally {
      setBusy(false)
    }
  }

  async function composeOnly() {
    if (!project) return
    setBusy(true)
    setError('')
    try {
      setProject(await api.compose(project.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.compositionFailed"))
      await handleBillingError(err, nav)
    } finally {
      setBusy(false)
    }
  }

  async function downloadFinal() {
    if (!project?.final_video_url) return
    setBusy(true)
    setError('')
    try {
      await downloadSingleVideo({ url: api.assetUrl(project.final_video_url, project.updated_at), title: project.title, projectId: project.id })
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("history.downloadFailed"))
    } finally {
      setBusy(false)
    }
  }

  async function onCoverFile(file: File | null) {
    if (!project || !file) return
    setBusy(true)
    setError('')
    try {
      setProject(await api.uploadCover(project.id, file))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotUploadTheCover"))
    } finally {
      setBusy(false)
      if (coverInputRef.current) coverInputRef.current.value = ''
    }
  }

  async function useFirstShotCover() {
    if (!project) return
    const first = [...(project.shots || [])]
      .sort((a, b) => a.shot_no - b.shot_no)
      .find((s) => s.image_url)
    if (!first?.image_url) {
      setError(translateUi("workflow.noShotImagesAvailable"))
      return
    }
    setBusy(true)
    setError('')
    try {
      setProject(await api.updateProject(project.id, { cover_url: first.image_url }))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSetTheCover"))
    } finally {
      setBusy(false)
    }
  }

  function openBatchAdjust() {
    if (!project) return
    setBatchSelected((project.shots || []).map((s) => s.id))
    setBatchDuration('')
    setBatchRegenAudio(false)
    setBatchOpen(true)
  }

  async function applyBatchAdjust() {
    if (!project || batchSelected.length === 0) return
    const durationVal = batchDuration.trim() === '' ? null : Number(batchDuration)
    if (durationVal != null && (!Number.isFinite(durationVal) || durationVal <= 0)) {
      setError(translateUi("workflow.enterAValidDurationInSeconds"))
      return
    }
    if (durationVal == null && !batchRegenAudio) {
      setError(translateUi("workflow.setADurationOrSelectNarrationRegeneration"))
      return
    }
    setBusy(true)
    setError('')
    try {
      // 先改完时长：配音任务一旦开始，整片级占用会挡住后续保存
      if (durationVal != null) {
        for (const shotId of batchSelected) {
          await api.updateShot(project.id, shotId, { duration: durationVal })
        }
      }
      const audioPlan = batchRegenAudio ? batchRegenAudioPlan(project, batchSelected) : null
      if (audioPlan) {
        await api.regenAudio(project.id, audioPlan.shotId, audioPlan.extraShotIds)
      }
      setProject(await api.getProject(project.id))
      setBatchOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotApplyBatchChanges"))
      try {
        setProject(await api.getProject(project.id))
      } catch {
        /* ignore */
      }
    } finally {
      setBusy(false)
    }
  }

  async function publish() {
    if (!project) return
    setBusy(true)
    try {
      await api.publish(project.id)
      nav('/history')
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotPublish"))
    } finally {
      setBusy(false)
    }
  }

  // 标记本镜请求进行中，不覆盖其它镜
  function markShotBusy(shotId: number) {
    setBusyShotIds((ids) => new Set(ids).add(shotId))
  }

  // 只清自己，避免并行请求互相冲掉锁
  function markShotIdle(shotId: number) {
    setBusyShotIds((ids) => {
      const next = new Set(ids)
      next.delete(shotId)
      return next
    })
  }

  async function regenImage(shot: Shot) {
    if (!project) return
    markShotBusy(shot.id)
    try {
      // 数字人：按该段场景描述重画「同一个人 + 场景」
      setProject(
        shotImageAction(project.pipeline_mode) === 'avatar_scene'
          ? await api.regenAvatarScene(project.id, shot.id)
          : await api.regenImage(project.id, shot.id),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.imageGenerationFailed_d0996d"))
    } finally {
      markShotIdle(shot.id)
    }
  }

  async function regenVideo(shot: Shot) {
    if (!project) return
    markShotBusy(shot.id)
    try {
      setProject(await api.regenVideo(project.id, shot.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.videoGenerationFailed_9a917d"))
    } finally {
      markShotIdle(shot.id)
    }
  }

  async function regenAudio(shot: Shot) {
    if (!project) return
    // 重配音会重写整片口播，锁整表避免两路抢写
    setBusy(true)
    try {
      setProject(await api.regenAudio(project.id, shot.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotRegenerateNarration"))
    } finally {
      setBusy(false)
    }
  }

  function openShotEdit(shot: Shot, focus = '') {
    setEditFocus(focus)
    if (focus === 'narration') setEditMode('narration')
    else if (focus === 'segment_script') setEditMode('segment')
    else setEditMode('full')
    setEditing({ ...shot })
    setMenuShotId(null)
  }

  function closeShotEdit() {
    setEditing(null)
    setEditFocus('')
    setEditMode('full')
  }

  // 改旁白时同步写入脚本旁白段
  function patchEditingNarration(value: string) {
    if (!editing) return
    const script = editing.segment_script || editing.video_prompt || ''
    const next = replaceNarrationInScript(script, value)
    setEditing({
      ...editing,
      narration: value,
      segment_script: next,
      video_prompt: next,
    })
  }

  // 改首帧画面时同步写入脚本第一段 visual
  function patchEditingVisual(value: string) {
    if (!editing) return
    const script = editing.segment_script || editing.video_prompt || ''
    const next = replaceFirstVisualInScript(script, value)
    setEditing({
      ...editing,
      img_prompt: value,
      segment_script: next,
      video_prompt: next,
    })
  }

  // 改脚本时回填旁白与首帧画面，并同步时长为 @duration 合计
  function patchEditingScript(value: string) {
    if (!editing) return
    const tagged = sumDuration(value)
    setEditing({
      ...editing,
      segment_script: value,
      video_prompt: value,
      narration: narrationFromScript(value),
      img_prompt: firstVisualFromScript(value) || editing.img_prompt,
      duration: tagged > 0 ? tagged : editing.duration,
    })
  }

  async function saveShot() {
    if (!project || !editing) return
    // scriptText 当前编辑中的逐段脚本
    const scriptText = editing.segment_script || editing.video_prompt || ''
    // durationCheck 时长校验结果
    const durationCheck = validateSegmentScriptDuration(scriptText)
    if (!durationCheck.valid) {
      setError(durationCheck.message || translateUi("workflow.invalidStoryboardDuration"))
      return
    }
    setBusy(true)
    try {
      // 数字人没有逐段脚本：只提交口播、小标题、场景描述与重点文字，避免脚本规范化改写字段
      const body: Partial<Shot> =
        project.pipeline_mode === 'avatar'
          ? {
              narration: editing.narration,
              overlay_title: editing.overlay_title,
              img_prompt: editing.img_prompt,
              highlights: editing.highlights || '',
            }
          : {
        narration: editing.narration,
        overlay_title: editing.overlay_title,
        overlay_subtitle: editing.overlay_subtitle,
        img_prompt: editing.img_prompt,
        video_prompt: editing.video_prompt,
        segment_script: editing.segment_script,
        duration: durationCheck.total > 0 ? durationCheck.total : Number(editing.duration) || 4,
        camera: editing.camera,
      }
      await api.updateShot(project.id, editing.id, body)
      closeShotEdit()
      setProject(await api.getProject(project.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSave"))
    } finally {
      setBusy(false)
    }
  }

  // 保存项目提示词；与后台模板相同则清空覆盖
  async function saveProjectPrompts() {
    if (!project || !promptEdit) return
    setBusy(true)
    try {
      const d = promptDefaults
      const styleOut = promptEdit.style_prompt.trim()
      const charOut = promptEdit.character_prompt.trim()
      const extraOut = promptEdit.extra_prompt.trim()
      const updated = await api.updateProject(project.id, {
        style_prompt: d && styleOut === d.style_prompt ? '' : styleOut,
        character_prompt: d && charOut === d.character_prompt ? '' : charOut,
        extra_prompt: d && extraOut === d.extra_prompt ? '' : extraOut,
      })
      setProject(updated)
      setPromptEdit(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSaveThePrompt"))
    } finally {
      setBusy(false)
    }
  }

  // 清空项目覆盖，后续生成跟随后台模板
  async function restoreTemplatePrompts() {
    if (!project) return
    setBusy(true)
    try {
      const updated = await api.updateProject(project.id, {
        style_prompt: '',
        character_prompt: '',
        extra_prompt: '',
      })
      setProject(updated)
      setPromptEdit(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotRestoreTheTemplate"))
    } finally {
      setBusy(false)
    }
  }

  if (!project && !error) {
    return (
      <AppShell active="studio">
        <p className="pf-muted">{translateUi("ecomSet.loading")}</p>
      </AppShell>
    )
  }

  if (!project) {
    return (
      <AppShell active="studio">
        <BillingErrorNotice message={error} />
      </AppShell>
    )
  }

  const structure = (project.shots || []).slice(0, 5).map((s, i) => {
    const labels = [translateUi("workflow.opening_e3aa56"), translateUi("workflow.development"), translateUi("workflow.turn"), translateUi("workflow.climax"), translateUi("workflow.ending")]
    return { label: labels[i] || translateUi("workflow.segment", { v1: String(i + 1) }), text: s.narration || s.overlay_title || '—' }
  })

  return (
    <AppShell active="studio" wide>
      {!isFullPipeline ? <>
        {error ? <BillingErrorNotice message={error} /> : null}
        {project.error_msg ? <p className="pf-error">{project.error_msg}</p> : null}
        <ImageTextStoryboard
          key={project.id}
          project={project}
          template={template}
          busy={busy}
          running={running}
          busyShotIds={busyShotIds}
          onBack={() => nav('/history')}
          onSettings={() => nav(`/studio/new?project=${project.id}`)}
          onGenerate={() => void continueGenerate()}
          onCompose={() => void composeOnly()}
          onCancel={() => void cancelGenerate()}
          onRestart={() => void restartGenerate()}
          onExport={() => downloadStoryboardCsv(project)}
          onDownload={() => void downloadFinal()}
          onEditor={() => nav(`/studio/${project.id}/editor`)}
          onEditShot={openShotEdit}
          onPreviewShot={(shot) => setPreview({ kind: 'shot', shotNo: shot.shot_no, imageUrl: shot.image_url, videoUrl: shot.video_url, audioUrl: shot.audio_url, caption: shotCaption(shot) })}
          onRegenImage={(shot) => void regenImage(shot)}
          onRegenAudio={(shot) => void regenAudio(shot)}
        />
        <ProjectMediaCostSummary project={project} />
      </> : <>
      <header className="pf-page-head">
        <div className="pf-page-head-row">
          <div>
            <button type="button" className="pf-back" onClick={() => nav(`/studio/${project.id}/style`)}>
              <IconChevronLeft size={18} />
              {translateUi("workflow.aiStoryboardWorkspace")}
            </button>
            <h1 className="pf-page-title">{project.title}</h1>
            <EcomSetSourceLink ecomSetId={project.ecom_set_id} />
          </div>
          <div className="pf-toolbar">
            {primaryAction === 'preview' ? (
              <button
                type="button"
                className="pf-btn pf-btn-lime pf-btn-sm pf-btn-icon"
                disabled={busy || !hasFinal}
                onClick={openFinalPreview}
              >
                <IconMonitor size={14} />
                {translateUi("workflow.previewVideo")}
              </button>
            ) : primaryAction === 'compose' ? (
              <button
                type="button"
                className="pf-btn pf-btn-lime pf-btn-sm pf-btn-icon"
                disabled={busy || running}
                onClick={composeOnly}
              >
                <IconPlay size={14} />
                {translateUi("workflow.composeVideo")}
              </button>
            ) : (
              <button
                type="button"
                className="pf-btn pf-btn-lime pf-btn-sm pf-btn-icon"
                disabled={busy || running}
                onClick={() =>
                  shots.length === 0 ? nav(`/studio/${project.id}/style`) : void continueGenerate()
                }
              >
                <IconPlay size={14} />
                {generateLabel}
              </button>
            )}
            {hasActiveUnifiedTasks(project) ? (
              <button
                type="button"
                className="pf-btn pf-btn-outline pf-btn-sm"
                disabled={busy || cancellationPending}
                onClick={cancelGenerate}
              >
                {cancellationPending ? translateUi("workflow.waitingToStop_ec905c") : translateUi("workflow.cancelGeneration")}
              </button>
            ) : null}
            <button
              type="button"
              className="pf-btn-text"
              disabled={busy || running || shots.length === 0}
              onClick={openBatchAdjust}
            >
              <IconSliders size={15} />
              {translateUi("workflow.batchEdit")}
            </button>
            <button
              type="button"
              className="pf-btn-text"
              disabled={busy || running}
              onClick={restartGenerate}
            >
              <IconRefresh size={15} />
              {translateUi("workflow.startOver")}
            </button>
            {primaryAction !== 'preview' && hasFinal ? (
              <button type="button" className="pf-btn-text" onClick={openFinalPreview}>
                <IconMonitor size={15} />
                {translateUi("workflow.previewVideo")}
              </button>
            ) : null}
            {primaryAction === 'preview' && readyToCompose ? (
              <button
                type="button"
                className="pf-btn-text"
                disabled={busy || running}
                onClick={composeOnly}
title={translateUi("workflow.composeAgainFromTheCurrentShots")}
            >
              {translateUi("workflow.recompose")}
            </button>
            ) : null}
            {primaryAction !== 'generate' && !readyToCompose ? (
              <button
                type="button"
                className="pf-btn-text"
                disabled={busy || running}
                onClick={continueGenerate}
              >
                {generateLabel === translateUi("workflow.generating") ? translateUi("workflow.continueGeneration") : generateLabel}
              </button>
            ) : null}
            <button
              type="button"
              className="pf-btn pf-btn-outline pf-btn-sm pf-btn-icon"
              onClick={() => nav(`/studio/${project.id}/editor`)}
            >
              <IconEdit size={14} />
              {translateUi("workflow.openEditor")}
            </button>
          </div>
        </div>
        <Stepper
          steps={kepuSteps(project.pipeline_mode)}
          current={step}
          doneThrough={Math.max(0, step - 1)}
        />
        <p className="pf-muted" style={{ fontSize: '0.78rem', margin: '0.55rem 0 0' }}>
          {kepuPhaseHint(project)}
        </p>
      </header>

      {error ? <BillingErrorNotice message={error} /> : null}
      <ProjectMediaCostSummary project={project} />
      {project.error_msg ? <p className="pf-error">{project.error_msg}</p> : null}

      <div className="pf-board">
        <aside className="pf-create-col">
          <h3>{translateUi("workflow.projectSettings")}</h3>
          {(project.cover_url || template?.preview_cover) ? (
            <img
              src={api.assetUrl(project.cover_url || template?.preview_cover)}
              alt=""
              style={{ width: '100%', borderRadius: 12, aspectRatio: '16/10', objectFit: 'cover' }}
            />
          ) : (
            <div
              style={{
                width: '100%',
                aspectRatio: '16/10',
                borderRadius: 12,
                background: '#e8eaee',
                display: 'grid',
                placeItems: 'center',
                color: 'var(--pf-muted)',
              }}
            >
              <IconImage size={28} />
            </div>
          )}
          <input
            ref={coverInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            hidden
            onChange={(e) => onCoverFile(e.target.files?.[0] || null)}
          />
          <div className="pf-side-actions">
            <button
              type="button"
              className="pf-btn pf-btn-ghost pf-btn-sm pf-btn-icon"
              disabled={busy || running}
              onClick={() => coverInputRef.current?.click()}
            >
              <IconImage size={14} />
              {translateUi("workflow.changeCover")}
            </button>
            <button
              type="button"
              className="pf-btn pf-btn-ghost pf-btn-sm pf-btn-icon"
              disabled={busy || running || !shots.some((s) => s.image_url)}
              onClick={useFirstShotCover}
              title={translateUi("workflow.useTheFirstShotWithAnImageAsThe")}
            >
              <IconImage size={14} />
              {translateUi("workflow.useFirstShotCover")}
            </button>
            <button
              type="button"
              className="pf-btn pf-btn-ghost pf-btn-sm pf-btn-icon"
              disabled={shots.length === 0}
              onClick={() => downloadStoryboardCsv(project)}
            >
              <IconDownload size={14} />
              {translateUi("workflow.exportDraft")}
            </button>
          </div>
          <ul className="pf-meta-list" style={{ marginTop: '0.85rem' }}>
            <li>
              <span>{translateUi("workflow.projectName")}</span>
              <span>{project.title}</span>
            </li>
            <li>
              <span>{translateUi("workflow.status")}</span>
              <span>{statusLabel(project)}</span>
            </li>
            <li>
              <span>{translateUi("workflow.progress")}</span>
              <span>{project.progress}%</span>
            </li>
            <li>
              <span>{translateUi("workflow.duration")}</span>
              <span>
                {Math.floor(totalDuration / 60)
                  .toString()
                  .padStart(2, '0')}
                :
                {Math.floor(totalDuration % 60)
                  .toString()
                  .padStart(2, '0')}
              </span>
            </li>
            <li>
              <span>{translateUi("workflow.aspectRatio_f07fc5")}</span>
              <span>{project.output_ratio || (project.pipeline_mode === 'image_text' ? '9:16' : '16:9')}</span>
            </li>
            <li>
              <span>{translateUi("workflow.videoMode")}</span>
              <span>{pipelineModeLabel(project.pipeline_mode)}</span>
            </li>
            <li>
              <span>{translateUi("ecomSet.form.style")}</span>
              <span>{template?.name || project.template_id}</span>
            </li>
          </ul>
          <button
            type="button"
            className="pf-btn pf-btn-ghost pf-btn-block pf-btn-sm"
            onClick={() => nav(`/studio/${project.id}/style`)}
            disabled={running}
          >
            {translateUi("workflow.editProjectSettings")}
          </button>
          <div className="pf-prompt-panel">
            <h4>{translateUi("workflow.builtInPrompts")}</h4>
            <p className="pf-muted" style={{ fontSize: '0.72rem', margin: '0 0 0.45rem' }}>
              {lt('生图使用模板画风参考图，并参考前镜画面延续角色、服装与色彩；每镜图片再用于生成视频。', 'Images use the template style reference and the previous shot to keep characters, outfits and colors consistent; each shot image is then used to generate video.')}
            </p>
            {(
              [
                [translateUi("ecomSet.form.style"), displayPrompts.style_prompt],
                [translateUi("workflow.character"), displayPrompts.character_prompt],
                [translateUi("workflow.additional"), displayPrompts.extra_prompt],
              ] as const
            ).map(([label, value]) => (
              <button
                key={label}
                type="button"
                className="pf-prompt-chip"
                disabled={busy || running}
                onClick={() =>
                  setPromptEdit({
                    style_prompt: displayPrompts.style_prompt,
                    character_prompt: displayPrompts.character_prompt,
                    extra_prompt: displayPrompts.extra_prompt,
                  })
                }
              >
                <strong>{label}</strong>
                <span>{(value || '').trim() || translateUi("workflow.emptyClickToEdit")}</span>
              </button>
            ))}
          </div>
          <p className="pf-muted" style={{ fontSize: '0.75rem', marginTop: '0.75rem' }}>
            {translateUi("workflow.aiGeneratedContentPleaseCheckItsAccuracy")}
          </p>
        </aside>

        <div className="pf-board-main">
          <div className="pf-outline-grid">
            <article className="pf-create-col">
              <h3>{translateUi("workflow.aiOutline")}</h3>
              <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.65 }}>
                {project.source_text}
              </p>
              <div className="pf-tags">
                <span>{translateUi("workflow.coreTopic")}</span>
                <span>{project.source_type === 'script' ? translateUi("workflow.fullScript") : translateUi("workflow.oneLineTopic")}</span>
              </div>
            </article>
            <article className="pf-create-col">
              <h3>{translateUi("workflow.structureSummary")}</h3>
              <ul className="pf-meta-list">
                {structure.length ? (
                  structure.map((s) => (
                    <li key={s.label}>
                      <span>{s.label}</span>
                      <span style={{ maxWidth: '60%', textAlign: 'right' }}>{s.text.slice(0, 36)}</span>
                    </li>
                  ))
                ) : (
                  <li>
                    <span>{translateUi("workflow.storyboardsPending")}</span>
                    <span>—</span>
                  </li>
                )}
              </ul>
            </article>
          </div>

          <section className="pf-shot-card">
            <div className="pf-shot-card-head">
              <h3>{translateUi("workflow.storyboardListScenes", { v1: project.shots.length })}</h3>
              <div className="pf-toolbar">
                {project.status === 'DONE' && hasFinal ? (
                  <button type="button" className="pf-btn pf-btn-lime pf-btn-sm" disabled={busy} onClick={publish}>
                    {translateUi("workflow.publish")}
                  </button>
                ) : null}
                {hasFinal ? (
                  <>
                    <button type="button" className="pf-btn pf-btn-ghost pf-btn-sm" onClick={openFinalPreview}>
                      {translateUi("workflow.previewVideo")}
                    </button>
                    <button type="button" className="pf-btn pf-btn-ghost pf-btn-sm" onClick={() => nav(withReturn(`/marketplace/from-printfilm?project=${project.id}`, `${location.pathname}${location.search}`))}>
                      发布到数字资产市场
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="pf-btn pf-btn-ghost pf-btn-sm"
                    disabled={busy || running || !readyToCompose}
                    onClick={composeOnly}
                  >
                    {translateUi("workflow.composeVideo")}
                  </button>
                )}
              </div>
            </div>
            {project.shots.length === 0 ? (
              <p className="pf-muted" style={{ margin: '1.5rem 0', textAlign: 'center' }}>
                {running
                  ? translateUi("workflow.planningStoryboards")
                  : translateUi("workflow.noStoryboardsYetStartFromStyleSettingsReviewThe")}
              </p>
            ) : needsScriptConfirm ? (
              <p className="pf-muted" style={{ margin: '0 0 1rem' }}>
                {translateUi("workflow.storyboardsAreReadyEditNarrationToUpdateSpeechAnd")}
              </p>
            ) : null}
            {project.shots.length === 0 ? null : (
              <ComplianceNotice
                fetchReport={() => getProjectCompliance(project.id)}
                refreshKey={complianceKey}
                applyDisabled={busy || running || Boolean(editing)}
                onApply={async (item, hit) => {
                  if (editing) throw new Error(translateUi("workflow.saveTheTextYouAreEditingFirst"))
                  const owner = item.scope === 'project' ? project : project.shots.find((shot) => shot.id === item.shot_id)
                  const current = owner ? String((owner as unknown as Record<string, unknown>)[item.field] || '') : ''
                  applyComplianceSuggestion(current, item.text, hit)
                  await applyProjectSuggestion(project.id, item, hit)
                  const updated = await api.getProject(project.id)
                  setProject((previous) => previous?.id === updated.id ? updated : previous)
                }}
                undoDisabled={busy || running}
                onUndoReplacement={async (row, change) => {
                  await undoProjectReplacement(project.id, row, change)
                  setProject(await api.getProject(project.id))
                }}
                describe={(item) => ({
                  label: kepuComplianceLabel(item, t),
                  onLocate: item.shot_id != null ? () => scrollToShotRow(item.shot_id as number) : undefined,
                })}
              />
            )}
            {project.shots.length === 0 ? null : (
              <div className="pf-shot-table-wrap">
                <table className="pf-shot-table">
                  <thead>
                    <tr>
                      <th className="col-no">{translateUi("workflow.scene")}</th>
                      <th className="col-thumb">{translateUi("workflow.visual")}</th>
                      <th className="col-narr">{translateUi("workflow.narrationDialogue")}</th>
                      <th className="col-seg">{translateUi("workflow.segmentScript")}</th>
                      <th className="col-dur">{translateUi("workflow.duration")}</th>
                      <th className="col-status">{translateUi("workflow.status")}</th>
                      <th className="col-ops">{translateUi("workflow.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shots.map((shot) => {
                      /*
                       * localBusy 本镜请求已发出、任务尚未回写
                       * shotGenerating 本镜任务或本地提交中
                       * pipelineLocked 整片流水线/配音占用
                       * displayKind / done / failed 按素材完备度展示
                       */
                      const localBusy = busyShotIds.has(shot.id)
                      const shotGenerating = isShotGenerating(project, shot.id) || localBusy
                      const pipelineLocked = isProjectWideBusy(project)
                      const rowBusy = busy || pipelineLocked || shotGenerating
                      const displayKind = shotDisplayKind(shot, {
                        pipelineMode: project.pipeline_mode,
                        generating: shotGenerating,
                      })
                      const done = shotDisplayDone(displayKind)
                      const failed = displayKind === 'failed'
                      const sceneTitle =
                        shot.overlay_title?.trim() || translateUi("workflow.scene_294057", { v1: String(String(shot.shot_no).padStart(2, '0')) })
                      const narration = (shot.narration || '').trim()
                      const script = shot.segment_script || shot.video_prompt || ''
                      const { cues, beats } = parseSegmentScript(script)
                      const desc = scenePromptForDisplay(shot.img_prompt).trim()
                      return (
                        <tr key={shot.id} id={shotRowDomId(shot.id)} className={liveProgress?.shot === shot.shot_no ? 'pf-shot-progress-current' : undefined}>
                          <td className="col-no">{String(shot.shot_no).padStart(2, '0')}</td>
                          <td className="col-thumb">
                            <button
                              type="button"
                              className="pf-shot-thumb-btn"
                              onClick={() =>
                                setPreview({
                                  kind: 'shot',
                                  shotNo: shot.shot_no,
                                  imageUrl: shot.image_url,
                                  videoUrl: shot.video_url,
                                  audioUrl: shot.audio_url,
                                  caption: shotCaption(shot),
                                })
                              }
                            >
                              {shot.image_url ? (
                                <img
                                  className="pf-shot-thumb"
                                  src={api.assetUrl(shot.image_url, shot.version)}
                                  alt=""
                                />
                              ) : (
                                <div className="pf-shot-thumb empty">
                                  {shotGenerating ? translateUi("ecomSet.result.running") : translateUi("workflow.imagePending_43ec51")}
                                </div>
                              )}
                            </button>
                          </td>
                          <td className="col-narr">
                            <button
                              type="button"
                              className="pf-shot-narration pf-shot-editable"
                              disabled={rowBusy}
                              title={translateUi("workflow.clickToEditNarrationAndTitle")}
                              onClick={() => openShotEdit(shot, 'narration')}
                            >
                              <span className="title">{sceneTitle}</span>
                              <span className="line">
                                {narration ? `“${narration}”` : '—'}
                              </span>
                              {shot.tts_metadata?.audio_url === shot.audio_url && shot.tts_metadata.is_fallback ?
                                <span className="pf-muted">{translateUi("workflow.thisSegmentUsesABackupVoice")}</span> : null}
                              {isAvatar && shot.highlights ? (
                                <span className="pf-muted" style={{ display: 'block', fontSize: '0.75rem' }}>
                                  {translateUi("workflow.highlights", { v1: shot.highlights.split('|').join(' · ') })}</span>
                              ) : null}
                            </button>
                          </td>
                          <td className="col-seg">
                            <button
                              type="button"
                              className="pf-shot-desc pf-shot-editable"
                              disabled={rowBusy}
                              title={translateUi("workflow.clickToEditTheSegmentScript")}
                              onClick={() => openShotEdit(shot, 'segment_script')}
                              style={{ textAlign: 'left', width: '100%' }}
                            >
                              {cues.length > 0 ? (
                                <span className="pf-muted" style={{ display: 'block', fontSize: '0.75rem' }}>
                                  {cues[0]?.replace(/^【|】$/g, '').slice(0, 28)}
                                  {cues[1]
                                    ? ` · ${cues[1].replace(/^【BGM：|】$/g, '').slice(0, 16)}`
                                    : ''}
                                </span>
                              ) : null}
                              {beats.length > 0 ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                                  {beats.slice(0, 4).map((b, i) => (
                                    <span key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                                      {b.duration > 0 ? (
                                        <span
                                          style={{
                                            flex: '0 0 auto',
                                            fontSize: '0.72rem',
                                            background: '#111',
                                            color: '#fff',
                                            borderRadius: 4,
                                            padding: '1px 5px',
                                          }}
                                        >
                                          {b.duration}s
                                        </span>
                                      ) : null}
                                      <span style={{ fontSize: '0.82rem' }}>
                                        {b.text.length > 42 ? `${b.text.slice(0, 42)}…` : b.text}
                                      </span>
                                    </span>
                                  ))}
                                  {beats.length > 4 ? (
                                    <span className="pf-muted" style={{ fontSize: '0.75rem' }}>
                                      {translateUi("workflow.moreSegments", { v1: beats.length - 4 })}</span>
                                  ) : null}
                                </div>
                              ) : (
                                desc || translateUi("workflow.clickToAddASegmentScript")
                              )}
                            </button>
                          </td>
                          <td className="col-dur">{formatMmSs(shotDisplayDurationSec(shot))}</td>
                          <td className="col-status">
                            <span
                              className={[
                                'pf-shot-status',
                                failed ? 'bad' : done ? '' : 'warn',
                              ]
                                .filter(Boolean)
                                .join(' ')}
                            >
                              {done && !failed ? <span className="mark">✓</span> : null}
                              {shotDisplayLabel(displayKind)}
                            </span>
                          </td>
                          <td className="col-ops">
                            <div className="pf-shot-ops">
                              <button
                                type="button"
                                className="op"
                                disabled={rowBusy}
                                onClick={() => openShotEdit(shot)}
                              >
                                {translateUi("workflow.edit")}
                              </button>
                              <button
                                type="button"
                                className="op"
                                disabled={rowBusy}
                                onClick={() => regenImage(shot)}
                              >
                                {isAvatar ? translateUi("workflow.redrawScene") : shot.image_url ? translateUi("workflow.redrawImage") : translateUi("workflow.generateImage_c9c7b0")}
                              </button>
                              {isFullPipeline ? (
                                <button
                                  type="button"
                                  className="op op-video"
                                  disabled={rowBusy || !shot.image_url}
                                  title={!shot.image_url ? translateUi("workflow.generateThisShotSImageFirst") : undefined}
                                  onClick={() => regenVideo(shot)}
                                >
                                  {shot.video_url ? translateUi("workflow.regenerateVideo") : translateUi("workflow.generateVideo_d2252f")}
                                </button>
                              ) : null}
                              {shot.video_url ? (
                                <button type="button" className="op" onClick={() => setPreview({ kind: 'shot', shotNo: shot.shot_no, imageUrl: shot.image_url, videoUrl: shot.video_url, audioUrl: shot.audio_url, caption: shotCaption(shot) })}>
                                  {lt('预览视频', 'Preview video')}
                                </button>
                              ) : null}
                              {/* 菜单目前只有重配音，内置口播时没有可用项 */}
                              {canRegenShotAudio(project) ? (
                                <button
                                  type="button"
                                  className="more"
                                  aria-label={translateUi("workflow.moreActions")}
                                  disabled={rowBusy}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setMenuShotId((id) => (id === shot.id ? null : shot.id))
                                  }}
                                >
                                  ⋮
                                </button>
                              ) : null}
                              {menuShotId === shot.id && canRegenShotAudio(project) ? (
                                <div className="pf-shot-menu" onClick={(e) => e.stopPropagation()}>
                                  <button
                                    type="button"
                                    disabled={rowBusy}
                                    onClick={() => {
                                      setMenuShotId(null)
                                      regenAudio(shot)
                                    }}
                                  >
                                    {translateUi("workflow.regenerateNarration")}
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="pf-shot-footer">
              <span className="pf-muted" style={{ fontSize: '0.82rem' }}>
                {lt(
                  `总时长: ${formatMmSs(totalDuration)} | 画面: ${project.shots.filter((s) => s.image_url).length}/${project.shots.length} 个 | 视频: ${project.shots.filter((s) => s.video_url).length}/${project.shots.length} 段 | 清晰度: ${project.video_resolution || '默认'}`,
                  `Duration: ${formatMmSs(totalDuration)} | Images: ${project.shots.filter((s) => s.image_url).length}/${project.shots.length} | Videos: ${project.shots.filter((s) => s.video_url).length}/${project.shots.length} | Resolution: ${project.video_resolution || 'default'}`,
                )}
              </span>
              <div className="pf-toolbar">
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost pf-btn-sm pf-btn-icon"
                  disabled={shots.length === 0}
                  onClick={() => downloadStoryboardCsv(project)}
                >
                  <IconDownload size={15} />
                  {translateUi("workflow.exportStoryboardScript")}
                </button>
                <button
                  type="button"
                  className="pf-btn-text"
                  disabled={busy || running}
                  onClick={deleteProject}
                >
                  <IconTrash size={15} />
                  {translateUi("workflow.deleteProject")}
                </button>
              </div>
            </div>
          </section>
        </div>

        <aside className="pf-create-col pf-board-settings">
          <h3>{translateUi("workflow.generationProgress")}</h3>
          {liveProgress?.connectionError ? <p className="pf-live-progress" role="status">{liveProgress.connectionError}</p> : null}
          {liveProgress?.message ? <p className="pf-live-progress" role="status">{liveProgress.message}</p> : null}
          <ul className="pf-progress-list">
            {progressItems.map((item) => (
              <li key={item.label}>
                <span>{item.label}</span>
                <span>
                  {item.done ? (
                    <span className="pf-check">✓</span>
                  ) : item.run ? (
                    `${item.pct ?? 0}%`
                  ) : (
                    '…'
                  )}
                </span>
              </li>
            ))}
          </ul>
          <div className="pf-meter" style={{ marginTop: '1rem' }}>
            <i style={{ width: `${Math.min(100, project.progress)}%` }} />
          </div>
          <p className="pf-muted" style={{ fontSize: '0.82rem' }}>
            {statusLabel(project)}
          </p>
        </aside>
      </div>

      </>}

      {batchOpen && project ? (
        <div className="modal-backdrop" onClick={() => !busy && setBatchOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{translateUi("workflow.batchEditStoryboardShots")}</h3>
            <p className="pf-muted" style={{ marginTop: 0 }}>
              {translateUi("workflow.selectedShots", { v1: batchSelected.length, v2: project.shots.length })}</p>
            <div
              style={{
                maxHeight: 160,
                overflow: 'auto',
                border: '1px solid var(--pf-border, #e5e7eb)',
                borderRadius: 10,
                padding: '0.5rem 0.75rem',
                marginBottom: '0.75rem',
              }}
            >
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                <input
                  type="checkbox"
                  checked={batchSelected.length === project.shots.length && project.shots.length > 0}
                  onChange={(e) =>
                    setBatchSelected(e.target.checked ? project.shots.map((s) => s.id) : [])
                  }
                />
                {translateUi("workflow.selectAll")}
              </label>
              {project.shots
                .slice()
                .sort((a, b) => a.shot_no - b.shot_no)
                .map((s) => (
                  <label
                    key={s.id}
                    style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}
                  >
                    <input
                      type="checkbox"
                      checked={batchSelected.includes(s.id)}
                      onChange={(e) =>
                        setBatchSelected((prev) =>
                          e.target.checked ? [...prev, s.id] : prev.filter((id) => id !== s.id),
                        )
                      }
                    />
                    {translateUi("workflow.shot_c6c9b1", { v1: String(s.shot_no).padStart(2, '0'), v2: formatMmSs(shotDisplayDurationSec(s)) })}</label>
                ))}
            </div>
            <label>
              {translateUi("workflow.durationForAllSelectedShotsSecondsBlankKeepsExisting")}
              <input
                type="number"
                min={1}
                step={0.5}
                placeholder={translateUi("workflow.forExample6")}
                value={batchDuration}
                onChange={(e) => setBatchDuration(e.target.value)}
              />
            </label>
            {canRegenShotAudio(project) ? (
              <label style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={batchRegenAudio}
                  onChange={(e) => setBatchRegenAudio(e.target.checked)}
                />
                {translateUi("workflow.regenerateNarrationForSelectedShots")}
              </label>
            ) : null}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
              <button
                type="button"
                className="pf-btn pf-btn-lime"
                disabled={busy || batchSelected.length === 0}
                onClick={applyBatchAdjust}
              >
                {translateUi("workflow.apply")}
              </button>
              <button
                type="button"
                className="pf-btn pf-btn-ghost"
                disabled={busy}
                onClick={() => setBatchOpen(false)}
              >
                {translateUi("ecomSet.copyModal.cancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {editing ? (
        <div className="modal-backdrop" onClick={closeShotEdit}>
          <div
            className={[
              'modal',
              'pf-prompt-modal',
              editMode === 'narration' ? 'pf-prompt-modal--narration' : '',
              editMode === 'segment' ? 'pf-prompt-modal--segment' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            onClick={(e) => e.stopPropagation()}
          >
            <h3>
              {editMode === 'narration'
                ? translateUi("workflow.editShotNarrationAndTitle", { v1: String(editing.shot_no) })
                : editMode === 'segment'
                  ? translateUi("workflow.editShotSegmentScript", { v1: String(editing.shot_no) })
                  : isFullPipeline
                    ? translateUi("workflow.editShotAllPrompts", { v1: String(editing.shot_no) })
                    : lt(`编辑镜头 ${editing.shot_no} · 画面与旁白`, `Edit shot ${editing.shot_no} · visual and narration`)}
            </h3>
            <div className="pf-prompt-modal-scroll">
              {editMode === 'narration' || editMode === 'full' ? (
                <>
                  <label>
                    {translateUi("workflow.shotTitle")}
                    <input
                      autoFocus={editFocus === 'title' || editMode === 'narration'}
                      value={editing.overlay_title || ''}
                      onChange={(e) => setEditing({ ...editing, overlay_title: e.target.value })}
                    />
                    <span className="pf-muted pf-prompt-hint">
                      {translateUi("workflow.nameInTheStoryboardListOverlaidAtTheTop")}
                    </span>
                  </label>
                  <label>
                    {translateUi("compliance.fields.overlay_subtitle")}
                    <input
                      value={editing.overlay_subtitle || ''}
                      onChange={(e) => setEditing({ ...editing, overlay_subtitle: e.target.value })}
                    />
                    <span className="pf-muted pf-prompt-hint">
                      {isFullPipeline ? lt('作为分镜说明使用', 'Used as the storyboard note') : lt('可选，会和标题一起显示在成片中', 'Optional; shown with the title in the final video')}
                    </span>
                  </label>
                  <label>
                    {translateUi("compliance.fields.narration")}
                    <textarea
                      autoFocus={editFocus === 'narration'}
                      value={editing.narration}
                      onChange={(e) => patchEditingNarration(e.target.value)}
                      rows={editMode === 'narration' ? 6 : 3}
                    />
                    <span className="pf-muted pf-prompt-hint">
                      {translateUi("workflow.syncsToNarrationInTheScriptAndAffectsThe")}
                    </span>
                  </label>
                  {isAvatar ? (
                    <>
                      <label>
                        {translateUi("workflow.onScreenSetting")}
                        <textarea
                          value={editing.img_prompt}
                          onChange={(e) => setEditing({ ...editing, img_prompt: e.target.value })}
                          rows={3}
                        />
                        <span className="pf-muted pf-prompt-hint">
                          {translateUi("workflow.describeTheBackgroundBehindTheSpeakerEnglishWorksMore")}
                        </span>
                      </label>
                      <label>
                        {translateUi("compliance.fields.highlights")}
                        <input
                          value={editing.highlights || ''}
                          placeholder={translateUi("workflow.separateUpToThreeTermsWithForExampleScattering")}
                          onChange={(e) => setEditing({ ...editing, highlights: e.target.value })}
                        />
                        <span className="pf-muted pf-prompt-hint">
                          {translateUi("workflow.showLargeYellowTextAboveTheSpeakerWhenEach")}
                        </span>
                      </label>
                    </>
                  ) : null}
                </>
              ) : null}
              {editMode === 'full' ? (
                <label>
                  {isFullPipeline ? translateUi("workflow.imagePromptFirstFrame") : lt('画面描述', 'Visual description')}
                  <textarea
                    autoFocus={editFocus === 'img_prompt'}
                    value={editing.img_prompt}
                    onChange={(e) => patchEditingVisual(e.target.value)}
                    rows={3}
                  />
                  <span className="pf-muted pf-prompt-hint">
                    {isFullPipeline ? translateUi("workflow.syncsToTheFirstVisualInTheScriptAnd") : lt('描述角色、场景和动作；修改后可重新生成这一张图片', 'Describe characters, scene and action; regenerate this image after editing')}
                  </span>
                </label>
              ) : null}
              {isFullPipeline && (editMode === 'segment' || editMode === 'full') ? (
                <>
                  <label>
                    {translateUi("workflow.segmentScriptVisualRhythmAndDuration")}
                    <textarea
                      className="pf-prompt-segment"
                      autoFocus={editFocus === 'segment_script' || editMode === 'segment'}
                      value={editing.segment_script || editing.video_prompt || ''}
                      onChange={(e) => patchEditingScript(e.target.value)}
                      rows={editMode === 'segment' ? 8 : 5}
                      placeholder={SEGMENT_SCRIPT_PLACEHOLDER}
                    />
                  </label>
                  <div className="pf-chips pf-prompt-duration-chips">
                    {editDurationCheck.durations.length > 0 ? (
                      editDurationCheck.durations.map((sec, i) => (
                        <span key={`${sec}-${i}`} className="pf-chip" style={{ cursor: 'default' }}>
                          {sec}s
                        </span>
                      ))
                    ) : (
                      <span className="pf-muted" style={{ fontSize: '0.8rem' }}>
                        {translateUi("workflow.noDurationTagsYet")}
                      </span>
                    )}
                    <span
                      className="pf-muted"
                      style={{
                        fontSize: '0.8rem',
                        marginLeft: 'auto',
                        color: editDurationCheck.valid ? undefined : 'var(--pf-danger, #c0392b)',
                      }}
                    >
                      {translateUi("workflow.totalSS", { v1: editDurationCheck.total, v2: SHOT_DURATION_MAX })}</span>
                  </div>
                  {!editDurationCheck.valid && editDurationCheck.message ? (
                    <p className="pf-error pf-prompt-duration-error">{editDurationCheck.message}</p>
                  ) : null}
                  <label>
                    {translateUi("workflow.cameraNotes")}
                    <input
                      value={editing.camera || ''}
                      onChange={(e) => setEditing({ ...editing, camera: e.target.value })}
                    />
                  </label>
                  <div className="pf-prompt-modal-row">
                    <label>
                      {translateUi("workflow.durationSecondsRecalculatedFromDurationOnSave")}
                      <input
                        type="number"
                        value={editing.duration}
                        onChange={(e) => setEditing({ ...editing, duration: Number(e.target.value) })}
                      />
                    </label>
                  </div>
                </>
              ) : null}
            </div>
            <div className="pf-prompt-modal-foot">
              {isFullPipeline && editMode !== 'segment' ? (
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost pf-btn-sm"
                  onClick={() => {
                    setEditMode('segment')
                    setEditFocus('segment_script')
                  }}
                >
                  {translateUi("workflow.segmentScript_a54c3b")}
                </button>
              ) : null}
              {isFullPipeline && editMode !== 'narration' ? (
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost pf-btn-sm"
                  onClick={() => {
                    setEditMode('narration')
                    setEditFocus('narration')
                  }}
                >
                  {translateUi("workflow.narrationAndTitle")}
                </button>
              ) : null}
              {isFullPipeline && editMode !== 'full' ? (
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost pf-btn-sm"
                  onClick={() => {
                    setEditMode('full')
                    setEditFocus('')
                  }}
                >
                  {translateUi("workflow.allFields")}
                </button>
              ) : null}
              <span className="pf-prompt-modal-foot-spacer" />
              <button
                type="button"
                className="pf-btn pf-btn-lime"
                disabled={busy || !editDurationCheck.valid}
                onClick={saveShot}
              >
                {translateUi("workflow.save")}
              </button>
              <button type="button" className="pf-btn pf-btn-ghost" onClick={closeShotEdit}>
                {translateUi("ecomSet.copyModal.cancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {promptEdit ? (
        <div className="modal-backdrop" onClick={() => setPromptEdit(null)}>
          <div className="modal pf-prompt-modal" onClick={(e) => e.stopPropagation()}>
            <h3>{translateUi("workflow.projectBuiltInPrompts")}</h3>
            <p className="pf-muted" style={{ fontSize: '0.8rem', marginTop: 0 }}>
              {translateUi("workflow.usesTheSiteTemplateByDefaultSavingMatchingText")}
            </p>
            <label>
              {translateUi("workflow.stylePrompt")}
              <textarea
                value={promptEdit.style_prompt}
                onChange={(e) => setPromptEdit({ ...promptEdit, style_prompt: e.target.value })}
                rows={3}
              />
            </label>
            <label>
              {translateUi("workflow.characterPrompt")}
              <textarea
                autoFocus
                value={promptEdit.character_prompt}
                onChange={(e) => setPromptEdit({ ...promptEdit, character_prompt: e.target.value })}
                rows={3}
              />
            </label>
            <label>
              {translateUi("workflow.extraRequirements")}
              <textarea
                value={promptEdit.extra_prompt}
                onChange={(e) => setPromptEdit({ ...promptEdit, extra_prompt: e.target.value })}
                rows={2}
              />
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="pf-btn pf-btn-lime"
                disabled={busy}
                onClick={saveProjectPrompts}
              >
                {translateUi("workflow.save")}
              </button>
              <button
                type="button"
                className="pf-btn pf-btn-ghost"
                disabled={busy}
                onClick={() => void restoreTemplatePrompts()}
              >
                {translateUi("workflow.restoreSiteTemplate")}
              </button>
              <button type="button" className="pf-btn pf-btn-ghost" onClick={() => setPromptEdit(null)}>
                {translateUi("ecomSet.copyModal.cancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {preview ? (
        <div className="modal-backdrop" onClick={() => setPreview(null)}>
          <div className="modal preview-modal" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0 }}>
                {preview.kind === 'final' ? preview.title : translateUi("workflow.shot_5d1002", { v1: String(preview.shotNo) })}
              </h3>
              <button type="button" className="pf-btn pf-btn-ghost pf-btn-sm" onClick={() => setPreview(null)}>
                {translateUi("workflow.close")}
              </button>
            </div>
            {preview.kind === 'final' ? (
              <video className="preview-media" src={preview.url} controls autoPlay />
            ) : preview.videoUrl ? (
              <video
                className="preview-media"
                src={api.assetUrl(preview.videoUrl)}
                poster={preview.imageUrl ? api.assetUrl(preview.imageUrl) : undefined}
                controls
                autoPlay
              />
            ) : preview.imageUrl ? (
              <img className="preview-media" src={api.assetUrl(preview.imageUrl)} alt="" />
            ) : (
              <p className="pf-muted">{translateUi("workflow.noPreviewYet_bdefa3")}</p>
            )}
            {preview.kind === 'shot' && preview.audioUrl ? (
              <audio src={api.assetUrl(preview.audioUrl)} controls style={{ width: '100%' }} />
            ) : null}
            {preview.kind === 'shot' ? <p className="pf-muted">{preview.caption}</p> : null}
          </div>
        </div>
      ) : null}
    </AppShell>
  )
}
import { ProjectMediaCostSummary } from '../../components/billing/ProjectMediaCostSummary'
