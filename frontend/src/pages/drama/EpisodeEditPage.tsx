import { applyComplianceSuggestion } from '../../lib/complianceChanges'
/** 分集编辑：复刻原项目四栏布局（资产 / 脚本 / 预览 / 故事板） */
import { MyTasksButton } from '../../components/MyTasks'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  dramaApi,
  resolveDramaMediaUrl,
  type DramaAsset,
  type DramaEpisode,
  type DramaFragment,
} from '../../api/drama'
import {
  duplicateEditableFragment, mergeFragmentRuntime,
} from '../../lib/dramaFragmentEditing'
import { createFragmentAutosave, type FragmentSaveState } from '../../lib/dramaFragmentAutosave'
import { type ImageStyleId } from '../../lib/dramaImageStyles'
import { selectedFragmentAssetIds, fragmentReferenceFreshness, fragmentReferenceStatusLabel } from '../../lib/dramaFragmentReferences'
import {
  RATIO_OPTIONS,
  buildFragmentRefStripItems,
  collectFragmentAssetIds,
  extractAssetIds,
  filterEpisodeAssets,
  formatFragLabel,
  fragmentQueueBadgeLabel,
  isFragmentGenerationBusy,
  normalizeAssetTab,
  readFragmentGenerationStatus,
  readFragmentVideoVersions,
  resolveFragmentDurationSec,
  type AssetScope,
  type AssetTab,
} from './dramaEpisodeEditUtils'
import {
  enqueueEpisodeVideoJobs,
  ensureEpisodeVideoStatusPoll,
  subscribeEpisodeGenerateStatus,
  syncEpisodeVideoJobs,
  useDramaGenQueue,
  videoJobId,
  type DramaGenJob,
} from '../../lib/dramaGenQueue'
import {
  collectDramaGenerateGateIssues,
  formatDramaGateMessage,
} from '../../lib/dramaEpisodeScriptValidate'
import { isMinimaxH3Model } from '../../lib/dramaMediaModelMeta'
import { DRAMA_VOICE_BINDING_ENABLED } from '../../lib/dramaVoiceBinding'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import ComplianceNotice from '../../components/compliance/ComplianceNotice'
import { getEpisodeCompliance } from '../../api/compliance'
import { dramaComplianceLabel, episodeComplianceKey } from '../../lib/compliance'
import { useI18n } from '../../i18n'
import { dialog } from '../../lib/dialog'
import {
  formatProjectOutputLabel,
  readEpisodeAspectRatio,
  readEpisodeResolution,
} from '../../lib/dramaProjectOutputSettings'
import { DramaFragmentClipSpec } from '../../components/drama/DramaFragmentClipSpec'
import { FragmentPlanSkillModal } from '../../components/drama/FragmentPlanSkillModal'
import { DramaGenTaskDetail } from '../../components/drama/DramaGenTaskDetail'
import { CircleAlert } from 'lucide-react'
import { useDramaImageGenQueue } from '../../hooks/useDramaImageGenQueue'
import { useMediaModelsCatalog } from '../../hooks/useMediaModelsCatalog'
import { enqueueDramaImageGen } from '../../lib/dramaImageGenQueue'
import { defaultOptionsForAssetKind } from '../../lib/dramaGenerationOptions'
import { dramaAssetImageGenButtonLabel } from '../../lib/dramaAssetImage'
import { readVisualPrompt } from '../../lib/dramaVisualPrompt'
import { generateAndBindCharacterVoice } from '../../lib/characterVoiceGenerate'
import { getImageStyleId } from './dramaWorkspaceUtils'
import { EpisodeEditAssetPanel } from './EpisodeEditAssetPanel'
import { EpisodeEditHeaderControls } from './EpisodeEditHeaderControls'
import { EpisodeEditPromptEditor } from './EpisodeEditPromptEditor'
import { EpisodeEditReferenceStrip } from './EpisodeEditReferenceStrip'
import { EpisodeEditSidePane } from './EpisodeEditSidePane'
import {
  GlobalAssetPickerModal,
  importGlobalAssetToProject,
} from './GlobalAssetPickerModal'
import {
  applySubtitleModeToFragments,
  subtitleModeUsesModelOutput,
  type DramaSubtitleMode,
} from '../../lib/dramaSubtitleBoard'
import {
  applyCharacterIntroModeToFragments,
  characterIntroModeEnabled,
  type DramaCharacterIntroMode,
} from '../../lib/dramaCharacterIntro'
import {
  readEffectiveCharacterIntroMode,
  readEffectiveSubtitleMode,
} from '../../lib/dramaProjectGlobalSettings'
import {
  CharacterVoiceBindModal,
  readAssetVoiceBinding,
} from './CharacterVoiceBindModal'
import { DramaAssetDetailModal } from './DramaAssetDetailModal'
import { buildEpisodeDirItems, DramaEpisodeDir } from './DramaEpisodeDir'
import RequireAuth from './RequireAuth'
import './drama.css'

export default function EpisodeEditPage() {
  return (
    <RequireAuth>
      <EpisodeEditInner />
    </RequireAuth>
  )
}

function readFragmentPlanStatus(ep: DramaEpisode | null): string {
  // 优先读取统一任务中心中的分镜规划任务；旧 params 状态作为兜底
  const active = (ep?.active_tasks || []).find((task) => task.task_type === 'fragment_plan')
  if (
    active &&
    !active.cancel_requested &&
    ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(active.status)
  ) {
    return 'generating'
  }
  const st = ep?.params?.fragment_plan_status
  return typeof st === 'string' ? st : ''
}

// 统一解析项目参数里的布尔值，兼容历史字符串/数字写法。
function coerceProjectBool(value: unknown, defaultValue: boolean): boolean {
  if (value == null) return defaultValue
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['1', 'true', 'yes', 'on'].includes(normalized)) return true
    if (['0', 'false', 'no', 'off', ''].includes(normalized)) return false
  }
  return Boolean(value)
}

// 分集编辑页主体
function EpisodeEditInner() {
  const { projectId, episodeId } = useParams()
  const pid = Number(projectId)
  const eid = Number(episodeId)
  const navigate = useNavigate()
  const { t , t: translateUi } = useI18n()
  /*
   * episode 分集
   * fragments 分镜
   * assets 资产
   * selectedIndex 当前分镜
   * assetScope / assetTab 侧栏筛选
   * editing 是否编辑模式
   * videoStyleId / modelId / aspectRatio 生成参数（UI）
   * busy / status / error 状态
   */
  const [episode, setEpisode] = useState<DramaEpisode | null>(null)
  // complianceKey 已保存分镜签名：保存/重新分镜/重新加载后变化即自动做广告法检查
  const complianceKey = useMemo(() => episodeComplianceKey(episode), [episode])
  const [episodeList, setEpisodeList] = useState<DramaEpisode[]>([])
  const [fragments, setFragmentState] = useState<DramaFragment[]>([])
  const [saveState, setSaveState] = useState<FragmentSaveState>({ dirty: false, saving: false, conflict: false })
  const [assets, setAssets] = useState<DramaAsset[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [assetScope, setAssetScope] = useState<AssetScope>('episode')
  const [assetTab, setAssetTab] = useState<AssetTab | null>('character')
  const [editing, setEditing] = useState(false)
  const [videoStyleId, setVideoStyleId] = useState<ImageStyleId | ''>('')
  const [modelId, setModelId] = useState('')
  const [includeUpdatedImages, setIncludeUpdatedImages] = useState(false)
  const mediaCatalog = useMediaModelsCatalog()
  const [aspectRatio, setAspectRatio] = useState<(typeof RATIO_OPTIONS)[number]>('9:16')
  // subtitleMode 本集字幕方式：模型自出 / 后期拼接（默认后期）
  const [subtitleMode, setSubtitleMode] = useState<DramaSubtitleMode>('post')
  // characterIntroMode 本集人物介绍叠字：模型叠字 / 关闭（默认关闭）
  const [characterIntroMode, setCharacterIntroMode] = useState<DramaCharacterIntroMode>('off')
  // linkLastFrame 是否用上一镜尾帧作本镜首帧（写入 project.params，默认开启）
  const [linkLastFrame, setLinkLastFrame] = useState(true)
  // projectParams 项目 params 缓存（镜间衔接 + 分集输出规格回退）
  const [projectParams, setProjectParams] = useState<Record<string, unknown>>({})
  // episodeParams 分集 params 缓存（画幅 / 清晰度写入此处）
  const [episodeParams, setEpisodeParams] = useState<Record<string, unknown>>({})
  // planModalOpen AI 重新分镜确认（含 Skill 勾选）
  const [planModalOpen, setPlanModalOpen] = useState(false)
  const [previewVersionId, setPreviewVersionId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [characterVoiceBusyIds, setCharacterVoiceBusyIds] = useState<Set<number>>(() => new Set())
  /*
   * detailAsset 左侧资产详情（编辑/重新生成/上传）
   * voiceBindAsset 音色绑定弹窗目标
   * imageGenQueue 全局生图队列快照
   */
  const [detailAsset, setDetailAsset] = useState<DramaAsset | null>(null)
  const [voiceBindAsset, setVoiceBindAsset] = useState<DramaAsset | null>(null)
  /** failReasonJob 底部分镜感叹号打开的失败原因 */
  const [failReasonJob, setFailReasonJob] = useState<DramaGenJob | null>(null)
  // assetCreateBusy / libraryPickerOpen 侧栏新建与导入
  const [assetCreateBusy, setAssetCreateBusy] = useState(false)
  const [libraryPickerOpen, setLibraryPickerOpen] = useState(false)
  const imageGenQueue = useDramaImageGenQueue()
  const dramaGenQueue = useDramaGenQueue()
  const applyStatusRef = useRef<(st: Awaited<ReturnType<typeof dramaApi.generateStatus>>) => Awaited<
    ReturnType<typeof dramaApi.generateStatus>
  >>(() => ({ episode_id: 0, done: 0, failed: 0, running: 0, total: 0, tasks: [], fragments: [] }))
  const projectParamsRef = useRef<Record<string, unknown>>({})
  const episodeParamsRef = useRef<Record<string, unknown>>({})

  const autosave = useMemo(() => createFragmentAutosave({
    persist: (rows, snapshot) => dramaApi.saveFragments(eid, rows.map((row, index) => ({
      ...row, id: row.id > 0 ? row.id : undefined, sort_order: index,
      duration_sec: resolveFragmentDurationSec(row.content, row.duration_sec),
      params: { ...row.params, user_edited: true },
    })), snapshot),
    onRows: setFragmentState,
    onSaved: setEpisode,
    onState: setSaveState,
    onError: (err) => setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSave")),
  }), [eid])
  // 自动保存不把尚未重生的视频当成最新创作输入；旧视频保留本页初载基线。
  const videoBaselines = useRef(new Map<number, DramaFragment>())
  function rememberVideoBaselines(rows: DramaFragment[], initial = false) {
    if (initial) videoBaselines.current.clear()
    for (const row of rows) {
      if (row.video && (initial || videoBaselines.current.get(row.id)?.video !== row.video)) {
        videoBaselines.current.set(row.id, structuredClone(row))
      }
    }
  }
  const currentEditor = useRef(autosave)
  currentEditor.current = autosave
  const setFragments = (update: DramaFragment[] | ((rows: DramaFragment[]) => DramaFragment[])) => autosave.runtime(update)
  const editFragments = (update: DramaFragment[] | ((rows: DramaFragment[]) => DramaFragment[])) => autosave.edit(update)
  useEffect(() => { autosave.activate(); return () => autosave.dispose() }, [autosave])
  useEffect(() => {
    if (!saveState.dirty && !saveState.saving) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [saveState.dirty, saveState.saving])

  useEffect(() => {
    // 目录到达后，把旧 Kie/方舟 id 换成后台默认视频模型
    if (!mediaCatalog) return
    const ids = mediaCatalog.video_models.map((m) => m.id)
    if (!ids.length) return
    setModelId((prev) => (ids.includes(prev) ? prev : mediaCatalog.defaults.video_model || ids[0]))
  }, [mediaCatalog])

  const selected = fragments[selectedIndex] || null
  const selectedDuration = selected?.duration_sec ?? 8
  // generatingIds 当前分集排队/生成中的分镜 id
  const generatingIds = useMemo(() => {
    const ids = new Set<number>()
    for (const task of episode?.active_tasks || []) {
      if (
        typeof task.episode_id === 'number' &&
        task.episode_id > 0 &&
        task.episode_id !== eid
      ) {
        continue
      }
      if (
        task.task_type === 'fragment_video' &&
        typeof task.fragment_id === 'number' &&
        !task.cancel_requested &&
        ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(task.status)
      ) {
        // 成片已 done：忽略残留活跃任务，避免徽标长期「生成中」
        const frag = fragments.find((f) => f.id === task.fragment_id)
        if (frag && readFragmentGenerationStatus(frag).status === 'done') continue
        if (!frag) continue
        ids.add(task.fragment_id)
      }
    }
    for (const frag of fragments) {
      if (!frag.id) continue
      const st = readFragmentGenerationStatus(frag).status
      if (st === 'done') continue
      if (isFragmentGenerationBusy(st)) ids.add(frag.id)
    }
    return ids
  }, [eid, fragments, episode?.active_tasks])
  // anyFragmentGenerating 本集是否有分镜在排队/生成（不锁编辑，仅锁批量生成）
  const anyFragmentGenerating = generatingIds.size > 0
  // selectedIsGenerating 当前选中镜是否正在生成
  const selectedIsGenerating = Boolean(selected?.id && generatingIds.has(selected.id))
  // selectedHasVideo 当前镜是否已有成片（用于「重新生成」文案）
  const selectedHasVideo = Boolean(selected?.video)
  // selectedVersions 当前镜历史成片
  const selectedVersions = useMemo(() => readFragmentVideoVersions(selected), [selected])
  // previewVersion 当前选中待预览的历史成片
  const previewVersion = useMemo(
    () => selectedVersions.find((ver) => ver.id === previewVersionId) ?? null,
    [previewVersionId, selectedVersions],
  )
  const previewVideoUrl = previewVersion ? resolveDramaMediaUrl(previewVersion.video) : null
  const previewPosterUrl = previewVersion?.cover ? resolveDramaMediaUrl(previewVersion.cover) : null
  // generateAllLocked 仅提交入队时锁定，生成过程不阻塞编辑
  const generateAllLocked = busy
  // planFragmentsLocked 仅本集有视频生成或 AI 分镜进行中时锁定
  const planFragmentsLocked =
    busy || anyFragmentGenerating || readFragmentPlanStatus(episode) === 'generating'
  const selectedRefIds = useMemo(
    () => new Set(collectFragmentAssetIds(selected)),
    [selected],
  )
  const selectedRefItems = useMemo(
    () =>
      buildFragmentRefStripItems(selected, assets, resolveDramaMediaUrl, (asset) => {
        const voice = readAssetVoiceBinding(asset)
        return voice ? { label: voice.label, url: voice.url } : null
      }),
    [selected, assets],
  )
  // selectedGateBlocking / selectedGateIssues 当前镜脚本/资产门禁
  const selectedGateBlocking = useMemo(
    () => collectDramaGenerateGateIssues(selected, assets).blocking,
    [selected, assets, translateUi],
  )
  const selectedGateIssues = useMemo(() => {
    const { blocking, warnings } = collectDramaGenerateGateIssues(selected, assets)
    return [...blocking, ...warnings]
  }, [selected, assets, translateUi])

  // prevLastFrameUrl 上一镜已落盘尾帧（衔接触发条件；兼容 generation / 历史版本字段）
  const prevLastFrameUrl = useMemo(() => {
    if (selectedIndex <= 0) return ''
    const prev = fragments[selectedIndex - 1]
    const params = prev?.params
    if (!params || typeof params !== 'object') return ''
    const rec = params as Record<string, unknown>
    const fromTop = rec.lastFrameUrl
    if (typeof fromTop === 'string' && fromTop.trim()) return fromTop.trim()
    const gen = rec.generation
    if (gen && typeof gen === 'object') {
      const fromGen = (gen as Record<string, unknown>).lastFrameUrl
      if (typeof fromGen === 'string' && fromGen.trim()) return fromGen.trim()
    }
    const versions = readFragmentVideoVersions(prev)
    for (const ver of versions) {
      if (ver.lastFrameUrl && ver.lastFrameUrl.trim()) return ver.lastFrameUrl.trim()
    }
    return ''
  }, [fragments, selectedIndex])

  // 开启尾帧衔接时：仅上一镜从未生成过才禁止；上一镜在生成/排队时本镜可入队等待
  const continuityBlockedReason = useMemo(() => {
    if (!linkLastFrame || selectedIndex <= 0) return ''
    const prev = fragments[selectedIndex - 1]
    if (!prev) return translateUi("workflow.thePreviousShotIsMissingContinuityIsUnavailable")
    if (prev.id && generatingIds.has(prev.id)) {
      return ''
    }
    const prevDone =
      Boolean((prev.video || '').trim()) ||
      readFragmentGenerationStatus(prev).status === 'done' ||
      Boolean(prevLastFrameUrl)
    if (!prevDone) {
      return translateUi("workflow.generateThePreviousShotAndWaitForItsLast")
    }
    return ''
  }, [linkLastFrame, selectedIndex, fragments, generatingIds, prevLastFrameUrl, translateUi])

  const continuityQueueHint = useMemo(() => {
    if (!linkLastFrame || selectedIndex <= 0) return ''
    const prev = fragments[selectedIndex - 1]
    if (!prev?.id || !generatingIds.has(prev.id)) return ''
    return translateUi("workflow.thePreviousShotIsGeneratingThisShotWillWait")
  }, [linkLastFrame, selectedIndex, fragments, generatingIds, translateUi])

  // selectedGenerateLocked 锁当前镜「生成」：忙/生成中/衔接/缺参考图等硬门禁
  const selectedGenerateLocked =
    busy ||
    selectedIsGenerating ||
    Boolean(continuityBlockedReason) ||
    selectedGateBlocking.length > 0

  // 持久化镜间衔接开关到项目 params，并让后端重排未开始任务
  async function handleLinkLastFrameChange(enabled: boolean) {
    const prevEnabled = linkLastFrame
    const prevParams = projectParams
    const nextParams = { ...projectParams, linkLastFrame: enabled }
    setLinkLastFrame(enabled)
    setProjectParams(nextParams)
    try {
      const updated = await dramaApi.updateProject(pid, { params: nextParams })
      if (!autosave.isActive()) return
      const updatedParams =
        updated.params && typeof updated.params === 'object'
          ? (updated.params as Record<string, unknown>)
          : (nextParams as Record<string, unknown>)
      setProjectParams(updatedParams)
      setLinkLastFrame(coerceProjectBool(updatedParams.linkLastFrame ?? updatedParams.link_last_frame, enabled))
      // 刷新本集任务态，让底部分镜条立刻反映串行/并行调整
      try {
        const ep = await dramaApi.getEpisode(eid)
        if (!autosave.isActive()) return
        if (Array.isArray(ep.fragments)) setFragments((local) => mergeFragmentRuntime(local, ep.fragments))
      } catch {
        /* ignore refresh errors */
      }
      setStatus(
        enabled
          ? translateUi("workflow.lastFrameContinuityEnabledUnstartedJobsNowRunIn")
          : translateUi("workflow.lastFrameContinuityDisabledUnstartedJobsMayRunConcurrently"),
      )
    } catch (err) {
      if (!autosave.isActive()) return
      setLinkLastFrame(prevEnabled)
      setProjectParams(prevParams)
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSaveContinuitySettings"))
    }
  }

  // 持久化本集画幅 / 清晰度到 episode.params；已有成片时提示重新生成（quiet=模型自动钳制时静默）
  async function handleEpisodeOutputChange(
    nextParams: Record<string, unknown>,
    opts?: { quiet?: boolean },
  ) {
    const prevRatio = readEpisodeAspectRatio(episodeParams, projectParams)
    const prevRes = readEpisodeResolution(episodeParams, projectParams)
    const nextRatio = readEpisodeAspectRatio(nextParams, projectParams)
    const nextRes = readEpisodeResolution(nextParams, projectParams)
    if (prevRatio === nextRatio && prevRes === nextRes) return

    const prevLabel = formatProjectOutputLabel(prevRatio, prevRes)
    const nextLabel = formatProjectOutputLabel(nextRatio, nextRes)
    const generatedCount = fragments.filter((frag) => Boolean(frag.video)).length
    const quiet = Boolean(opts?.quiet)

    if (!quiet) {
      const ok = await dialog.confirm({
        title: translateUi("workflow.changeAspectRatioResolution"),
        message:
          generatedCount > 0
            ? translateUi("workflow.changeThisEpisodeFromToItsExistingClipsWill", { v1: String(prevLabel), v2: String(nextLabel), v3: String(generatedCount) })
            : translateUi("workflow.changeThisEpisodeFromToFutureStoryboardClipsWill", { v1: String(prevLabel), v2: String(nextLabel) }),
        confirmText: generatedCount > 0 ? translateUi("workflow.saveAndRegenerate") : translateUi("workflow.save"),
        cancelText: translateUi("ecomSet.copyModal.cancel"),
        tone: generatedCount > 0 ? 'danger' : 'default',
      })
      if (!ok || !autosave.isActive()) return
    }

    const prevParams = episodeParams
    const outputEpoch = autosave.epoch()
    setEpisodeParams(nextParams)
    episodeParamsRef.current = nextParams
    setAspectRatio(nextRatio)
    try {
      const updated = await dramaApi.updateEpisode(eid, { params: nextParams })
      if (!autosave.isActive()) return
      const updatedParams =
        updated.params && typeof updated.params === 'object' && !Array.isArray(updated.params)
          ? (updated.params as Record<string, unknown>)
          : nextParams
      setEpisode(updated)
      setEpisodeParams(updatedParams)
      episodeParamsRef.current = updatedParams
      setAspectRatio(readEpisodeAspectRatio(updatedParams, projectParams))
      if (!quiet) {
        setStatus(
          generatedCount > 0
            ? translateUi("workflow.episodeOutputUpdatedToRequeuingWithTheNewSettings", { v1: String(nextLabel) })
            : translateUi("workflow.episodeOutputUpdatedTo", { v1: String(nextLabel) }),
        )
        setError('')
        if (generatedCount > 0 && autosave.epoch() === outputEpoch) {
          await generateAll({ forceRegen: true, skipConfirm: true })
        }
      } else if (generatedCount > 0) {
        // 模型能力自动钳制：不弹确认、不重入队，但提示已生成镜头需手动重生成
        setStatus(translateUi("workflow.outputAdjustedToForThisModelRegenerateExistingClips", { v1: String(nextLabel) }))
      }
    } catch (err) {
      if (!autosave.isActive()) return
      setEpisodeParams(prevParams)
      episodeParamsRef.current = prevParams
      setAspectRatio(readEpisodeAspectRatio(prevParams, projectParams))
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSaveEpisodeOutputSettings"))
      throw err
    }
  }

  // 设置与创作输入共享同一 epoch；等待设置时不提交旧的分镜快照。
  async function saveContentSetting(nextParams: Record<string, unknown>, transform: (rows: DramaFragment[]) => DramaFragment[], message: string) {
    const editor = autosave
    const prevParams = episodeParamsRef.current
    const prevRows = editor.rows()
    const release = editor.hold()
    editor.edit(transform)
    const settingEpoch = editor.epoch()
    setBusy(true)
    setEpisodeParams(nextParams)
    episodeParamsRef.current = nextParams
    setSubtitleMode(readEffectiveSubtitleMode(nextParams, projectParamsRef.current))
    setCharacterIntroMode(readEffectiveCharacterIntroMode(nextParams, projectParamsRef.current))
    let settingSaved = false
    try {
      const updated = await dramaApi.updateEpisode(eid, { params: nextParams })
      if (!editor.isActive() || currentEditor.current !== editor) return
      settingSaved = true
      const params = (updated.params || nextParams) as Record<string, unknown>
      setEpisodeParams(params)
      episodeParamsRef.current = params
      release()
      await editor.flush()
      if (!editor.isActive()) return
      setStatus(message)
      setError('')
    } catch (err) {
      if (!editor.isActive()) return
      // 设置未保存时只回滚同一份稿；等待期间的新输入始终保留。
      if (!settingSaved && editor.epoch() === settingEpoch) {
        editor.edit(prevRows)
        setEpisodeParams(prevParams)
        episodeParamsRef.current = prevParams
        setSubtitleMode(readEffectiveSubtitleMode(prevParams, projectParamsRef.current))
        setCharacterIntroMode(readEffectiveCharacterIntroMode(prevParams, projectParamsRef.current))
      }
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSaveSettings"))
    } finally {
      release()
      if (editor.isActive()) setBusy(false)
    }
  }

  async function handleEpisodeSubtitleChange(mode: DramaSubtitleMode) {
    if (mode === subtitleMode) return
    await saveContentSetting({ ...episodeParamsRef.current, subtitleMode: mode, subtitleEnabled: subtitleModeUsesModelOutput(mode) },
      (rows) => applySubtitleModeToFragments(rows, mode),
      mode === 'model' ? translateUi("workflow.switchedToModelGeneratedSubtitlesAndRestoredTheStoryboard") : translateUi("workflow.switchedToPostProductionSubtitlesAndRemovedTheStoryboard"))
  }

  async function handleEpisodeCharacterIntroChange(mode: DramaCharacterIntroMode) {
    if (mode === characterIntroMode) return
    await saveContentSetting({ ...episodeParamsRef.current, characterIntroMode: mode, characterIntroEnabled: characterIntroModeEnabled(mode) },
      (rows) => applyCharacterIntroModeToFragments(rows, mode),
      mode === 'model' ? translateUi("workflow.characterIntroductionOverlaysEnabledReplanningWillAddTheIntroduction") : translateUi("workflow.characterIntroductionsDisabledAndTheirOverlayLinesRemoved"))
  }

  const referencedIds = useMemo(() => {
    const set = new Set<number>()
    for (const f of fragments) {
      for (const id of extractAssetIds(f.content || '')) set.add(id)
      for (const id of f.asset_ids || []) set.add(id)
    }
    return set
  }, [fragments])

  const filteredAssets = useMemo(
    () => filterEpisodeAssets(assets, assetScope, assetTab, referencedIds),
    [assets, assetScope, assetTab, referencedIds],
  )

  // 应用后端生成进度并同步分镜状态 + 全局队列
  function applyGenerateStatus(st: Awaited<ReturnType<typeof dramaApi.generateStatus>>) {
    setStatus(translateUi("workflow.doneRunningFailed", { v1: String(st.done), v2: String(st.total), v3: String(st.running), v4: String(st.failed) }))
    const byId = new Map(st.fragments.map((f) => [f.fragment_id, f.status]))
    setFragments((prev) => {
      const next = prev.map((f) => {
        if (!f.id) return f
        const genStatus = byId.get(f.id)
        if (!genStatus) return f
        const item = st.fragments.find((x) => x.fragment_id === f.id) as
          | {
              fragment_id: number
              status: string
              video?: string
              cover?: string
              message?: string
              phase?: string
              error?: string
            }
          | undefined
        return {
          ...f,
          video: item?.video || f.video,
          cover: item?.cover || f.cover,
          params: {
            ...(f.params || {}),
            generation: {
              status: genStatus,
              video: item?.video,
              cover: item?.cover,
              message: item?.message,
              phase: item?.phase,
              error: item?.error,
            },
          },
        }
      })
      syncEpisodeVideoJobs({
        projectId: pid,
        episodeId: eid,
        episodeName: episode?.name,
        fragments: next.map((f) => ({
          id: f.id,
          sort_order: f.sort_order,
          content: f.content,
        })),
        statusItems: st.fragments.map((f) => {
          const row = f as {
            fragment_id: number
            status: string
            message?: string
            phase?: string
            error?: string
            video?: string
            cover?: string
          }
          return {
            fragment_id: row.fragment_id,
            status: row.status,
            message: row.message,
            phase: row.phase,
            error: row.error,
            video: row.video,
            cover: row.cover,
          }
        }),
        taskItems: st.tasks,
      })
      return next
    })
    return st
  }

  applyStatusRef.current = applyGenerateStatus

  // 进页检查是否有进行中的生成任务或 AI 分镜
  async function resumeGenerateIfNeeded() {
    try {
      const ep = await dramaApi.getEpisode(eid)
      if (!autosave.isActive()) return
      if (readFragmentPlanStatus(ep) === 'generating') {
        const planEpoch = autosave.epoch()
        setBusy(true)
        setStatus(translateUi("workflow.waitingForGeneration"))
        const started = Date.now()
        while (Date.now() - started < 10 * 60 * 1000) {
          await new Promise((r) => setTimeout(r, 2500))
          const cur = await dramaApi.getEpisode(eid)
          if (!autosave.isActive()) return
          const st = readFragmentPlanStatus(cur)
          if (st === 'completed') {
            if (!autosave.isActive()) return
            if (autosave.state().dirty || autosave.epoch() !== planEpoch) {
              setError(translateUi("workflow.theStoryboardHasChangedYourLocalDraftIsKept"))
              setBusy(false)
              return
            }
            rememberVideoBaselines(cur.fragments || [], true)
            autosave.load(cur)
            setEpisode(cur)
            setSelectedIndex(0)
            setStatus(translateUi("workflow.storyboardCompleteClips", { v1: String((cur.fragments || []).length) }))
            setBusy(false)
            return
          }
          if (st === 'failed') {
            setError(String(cur.params?.fragment_plan_error || translateUi("workflow.aiStoryboardPlanningFailed")))
            setBusy(false)
            return
          }
          setStatus(translateUi("workflow.waitingForGeneration"))
        }
        setBusy(false)
        return
      }
      const result = await dramaApi.generateStatus(eid)
      if (!autosave.isActive()) return
      const st = applyGenerateStatus(result)
      if (st.running > 0) ensureEpisodeVideoStatusPoll()
    } catch {
      /* ignore */
    }
  }

  // 打开全屏分镜故事板画布
  async function openEpisodeStoryboard() {
    try { await save(); navigate(`/drama/projects/${pid}/episodes/${eid}/canvas`) } catch { /* 保留本地稿 */ }
  }

  // 重新加载分集
  async function reload(isCurrent = () => true) {
    const ep = await dramaApi.getEpisode(eid)
    if (!autosave.isActive() || !isCurrent()) return
    rememberVideoBaselines(ep.fragments || [], true)
    autosave.load({ ...ep, fragments: ep.fragments?.length ? ep.fragments : [{
      id: 0, episode_id: eid, sort_order: 0, content: '', cover: '', video: '', duration_sec: 8, asset_ids: [],
    }] })
    setEpisode(ep)
    const epParams =
      ep.params && typeof ep.params === 'object' && !Array.isArray(ep.params)
        ? (ep.params as Record<string, unknown>)
        : {}
    setEpisodeParams(epParams)
    episodeParamsRef.current = epParams
    setSubtitleMode(readEffectiveSubtitleMode(epParams, projectParamsRef.current))
    setCharacterIntroMode(readEffectiveCharacterIntroMode(epParams, projectParamsRef.current))
    setAspectRatio(readEpisodeAspectRatio(epParams, projectParamsRef.current))
    await resumeGenerateIfNeeded()
  }

  // 复用全局 generate_status 轮询，避免与 dramaGenQueue 重复请求
  useEffect(() => {
    return subscribeEpisodeGenerateStatus(async (episodeId, st) => {
      if (episodeId !== eid || !autosave.isActive() || currentEditor.current !== autosave) return
      const result = applyStatusRef.current(st)
      if (result.running === 0) {
        const ep = await dramaApi.getEpisode(eid)
        if (!autosave.isActive()) return
        rememberVideoBaselines(ep.fragments || [])
        setFragments((local) => mergeFragmentRuntime(local, ep.fragments || []))
      }
    })
  }, [eid])

  // 切换分镜时退出历史版本预览
  useEffect(() => {
    setPreviewVersionId(null)
  }, [selectedIndex, selected?.id])

  useEffect(() => {
    if (!eid || !pid) return
    let live = true
    setBusy(false)
    setStatus('')
    setError('')
    reload(() => live).catch((err) => { if (live && autosave.isActive()) setError(err instanceof Error ? err.message : translateUi("ecomSet.loadFailed")) })
    dramaApi
      .listAssets(pid)
      .then((rows) => { if (live && autosave.isActive()) setAssets(rows) })
      .catch(() => { if (live && autosave.isActive()) setAssets([]) })
    dramaApi
      .listEpisodes(pid)
      .then((rows) => { if (live && autosave.isActive()) setEpisodeList(rows) })
      .catch(() => { if (live && autosave.isActive()) setEpisodeList([]) })
    // 加载项目默认画面风格 / 镜间衔接等到顶栏
    dramaApi
      .getProject(pid)
      .then((project) => {
        if (!live || !autosave.isActive()) return
        const styleId = getImageStyleId(project.script, project)
        if (styleId) setVideoStyleId(styleId as ImageStyleId)
        const params =
          project.params && typeof project.params === 'object' && !Array.isArray(project.params)
            ? (project.params as Record<string, unknown>)
            : {}
        setProjectParams(params)
        projectParamsRef.current = params
        const linkRaw = params.linkLastFrame ?? params.link_last_frame
        setLinkLastFrame(coerceProjectBool(linkRaw, true))
        setSubtitleMode(readEffectiveSubtitleMode(episodeParamsRef.current, params))
        setCharacterIntroMode(readEffectiveCharacterIntroMode(episodeParamsRef.current, params))
        setAspectRatio(readEpisodeAspectRatio(episodeParamsRef.current, params))
      })
      .catch(() => {
        /* ignore */
      })
    return () => { live = false }
  }, [eid, pid])

  // 更新当前分镜
  function updateSelected(patch: Partial<DramaFragment>) {
    editFragments((prev) =>
      prev.map((f, i) => (i === selectedIndex ? { ...f, ...patch } : f)),
    )
  }

  // 仅交换角色之间的优先级，保留正文与所有关联资产，不靠重写剧情排序。
  function moveCharacterReference(assetId: number, direction: -1 | 1) {
    if (!selected || busy) return
    const ids = collectFragmentAssetIds(selected)
    const characterIds = ids.filter((id) => assets.some((asset) => asset.id === id && asset.type.trim().toLowerCase() === 'character'))
    const index = characterIds.indexOf(assetId)
    const other = characterIds[index + direction]
    if (index < 0 || other == null) return
    const left = ids.indexOf(assetId)
    const right = ids.indexOf(other)
    ;[ids[left], ids[right]] = [ids[right], ids[left]]
    updateSelected({ asset_ids: ids, params: { ...selected.params, reference_asset_order: ids } })
    setEditing(true)
    setStatus(translateUi("workflow.characterPriorityChangedPleaseSave"))
  }

  // 插入空白分镜
  function insertFrag(at: number) {
    editFragments((prev) => {
      const next = [...prev]
      next.splice(at, 0, {
        id: 0,
        episode_id: eid,
        sort_order: at,
        content: '',
        cover: '',
        video: '',
        duration_sec: 8,
        asset_ids: [],
      })
      return next
    })
    setSelectedIndex(at)
    setEditing(true)
  }

  // 复制分镜
  function duplicateFrag(index: number) {
    const source = fragments[index]
    if (!source) return
    editFragments((prev) => {
      const next = [...prev]
      next.splice(index + 1, 0, {
        ...duplicateEditableFragment(source),
        sort_order: index + 1,
      })
      return next
    })
    setSelectedIndex(index + 1)
  }

  // 删除分镜
  function deleteFrag(index: number) {
    if (fragments.length <= 1) return
    editFragments((prev) => prev.filter((_, i) => i !== index))
    setSelectedIndex((cur) => {
      if (cur === index) return Math.max(0, index - 1)
      if (cur > index) return cur - 1
      return cur
    })
  }

  // 手动保存、自动保存、生成和导航都等待同一份最新稿（包括在途新增的 B）。
  async function save() {
    const editor = autosave
    setError('')
    try {
      const ep = await editor.flush()
      if (!editor.isActive()) throw new Error(translateUi("workflow.theEpisodeChanged"))
      setStatus(translateUi("ecomSet.plan.saved"))
      return ep
    } catch (err) {
      if (editor.isActive()) setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSave"))
      throw err
    }
  }

  // 补参考图前先沿用当前保存入口；保存失败时保留本页和本地稿。
  async function openReferenceAssets() {
    if (busy) return
    try {
      await save()
      navigate(`/drama/projects/${pid}`, { state: { activeStep: 'assets' } })
    } catch {
      // save 已展示具体原因，失败不离开编辑页。
    }
  }

  // 判断「批量生成」时是否可安全跳过：仅跳过未改动且已有成片的旧分镜
  function shouldSkipGenerateAllFragment(frag: DramaFragment): boolean {
    if (!frag.video || !frag.id) return false
    const freshness = fragmentReferenceFreshness(frag, assets)
    if (freshness === 'inputs_changed' || (includeUpdatedImages && freshness === 'image_updated')) return false
    const input = frag.params?.video_input_snapshot as { content?: string; duration_sec?: number } | undefined
    const prev = videoBaselines.current.get(frag.id)
    if (!input && !prev?.video) return false
    const sameContent = (input?.content ?? prev?.content ?? '') === (frag.content || '')
    const sameDuration =
      resolveFragmentDurationSec(input?.content ?? prev?.content ?? '', input?.duration_sec ?? prev?.duration_sec) ===
      resolveFragmentDurationSec(frag.content || '', frag.duration_sec)
    const prevAssetIds = selectedFragmentAssetIds(prev || frag, assets)
    const nextAssetIds = selectedFragmentAssetIds(frag, assets)
    const sameAssets =
      prevAssetIds.length === nextAssetIds.length &&
      prevAssetIds.every((id, index) => id === nextAssetIds[index])
    return sameContent && sameDuration && sameAssets
  }

  // 仅生成当前选中分镜（保存按 id 更新，生成前仍用保存后返回的 id）
  async function generateSelected() {
    if (!selected) {
      setError(translateUi("workflow.selectAStoryboardShotFirst"))
      return
    }
    if (selectedGenerateLocked) {
      if (selectedGateBlocking.length > 0) {
        setError(selectedGateBlocking.map((i) => i.message).join('；'))
        await dialog.alert({
          title: translateUi("workflow.cannotGenerate"),
          message: formatDramaGateMessage(
            selectedGateBlocking,
            [],
            translateUi("workflow.generateOrUploadReferenceImagesForTheLinkedCharacters"),
          ),
        })
      } else if (continuityBlockedReason) {
        setError(continuityBlockedReason)
        await dialog.alert({
          title: translateUi("workflow.cannotGenerate"),
          message: translateUi("workflow.withLastFrameContinuityEnabledFinishThePreviousShot", { v1: String(continuityBlockedReason) }),
        })
      } else if (selectedIsGenerating) {
        setError(translateUi("workflow.thisStoryboardShotIsGeneratingWaitForItTo"))
      } else {
        setError(translateUi("workflow.waitForTheCurrentOperationToFinishBeforeTrying"))
      }
      return
    }

    const { blocking, warnings } = collectDramaGenerateGateIssues(selected, assets)
    if (blocking.length > 0) {
      setError(blocking.map((i) => i.message).join('；'))
      await dialog.alert({
        title: translateUi("workflow.cannotGenerate"),
        message: formatDramaGateMessage(blocking, warnings, translateUi("workflow.correctTheScriptUsingTheEpisodeRulesBeforeGenerating")),
      })
      return
    }

    const targetKey = selected.clientKey
    const fragLabel = formatFragLabel(selectedIndex, selectedDuration)
    const isRegen = Boolean(selected.video)
    const ok = await dialog.confirm({
      title: isRegen ? translateUi("workflow.regenerateStoryboardClip") : translateUi("workflow.generateStoryboardClip"),
      message: formatDramaGateMessage(
        [],
        warnings,
        isRegen
          ? translateUi("workflow.saveAndRegenerateTheCurrentClipWillBeKept", { v1: String(fragLabel) })
          : linkLastFrame
            ? continuityQueueHint
              ? translateUi("workflow.saveAndQueue", { v1: String(fragLabel), v2: String(continuityQueueHint) })
              : translateUi("workflow.saveAndGenerateInShotOrderYouCanKeep", { v1: String(fragLabel) })
            : translateUi("workflow.saveAndGenerateIndependentlyYouCanKeepEditingAfter", { v1: String(fragLabel) }),
      ),
      confirmText: warnings.length > 0 ? translateUi("workflow.generateAnyway") : isRegen ? translateUi("ecomSet.result.regenerate") : translateUi("ecomSet.confirm.ok"),
    })
    if (!ok) return
    setBusy(true)
    setError('')
    setStatus(isRegen ? translateUi("workflow.savingAndRequeuing") : translateUi("workflow.savingAndQueuingThisStoryboardShot"))
    try {
      const ep = await save()
      setBusy(true)
      const frag = autosave.rows().find((row) => row.clientKey === targetKey)
      if (!frag?.id) {
        throw new Error(translateUi("workflow.theCurrentStoryboardShotWasNotFoundAfterSaving"))
      }
      await dramaApi.generateEpisode(eid, [frag.id], modelId)
      // 乐观写入排队态，避免旧 video 把状态盖成已完成
      setFragments((prev) =>
        prev.map((f) =>
          f.id === frag.id
            ? {
                ...f,
                params: {
                  ...(f.params || {}),
                  generation: { status: 'queued', message: translateUi("workflow.queued") },
                },
              }
            : f,
        ),
      )
      enqueueEpisodeVideoJobs({
        projectId: pid,
        episodeId: eid,
        episodeName: ep.name || episode?.name,
        fragments: (ep.fragments || []).map((f, i) => ({
          id: f.id,
          sort_order: f.sort_order ?? i,
        })),
        fragmentIds: [frag.id],
      })
      setBusy(false)
      setStatus(isRegen ? translateUi("workflow.requeuedSeeTheQueueAtTheLowerRight", { v1: String(fragLabel) }) : translateUi("workflow.queuedSeeTheQueueAtTheLowerRight", { v1: String(fragLabel) }))
      ensureEpisodeVideoStatusPoll()
    } catch (err) {
      if (!autosave.isActive()) return
      setBusy(false)
      setError(err instanceof Error ? err.message : translateUi("ecomSet.result.failed"))
    }
  }

  // 切换历史成片为当前预览视频
  async function activateVideoVersion(versionId: string) {
    if (!selected?.id || selectedIsGenerating) return
    const ok = await dialog.confirm({
      title: translateUi("workflow.switchVideoVersion"),
      message: translateUi("workflow.useThisHistoricalClipAsTheCurrentVideoThe"),
      confirmText: translateUi("workflow.switch"),
    })
    if (!ok) return
    setBusy(true)
    setError('')
    try {
      const result = await dramaApi.activateFragmentVideoVersion(selected.id, versionId)
      if (!autosave.isActive()) return
      setFragments((prev) =>
        prev.map((f) =>
          f.id === selected.id
            ? {
                ...f,
                video: result.video,
                cover: result.cover || '',
                params: {
                  ...(f.params || {}),
                  video_versions: result.video_versions,
                  reference_snapshot: result.reference_snapshot || undefined,
                  video_input_snapshot: result.video_input_snapshot || undefined,
                  lastFrameUrl: result.lastFrameUrl || undefined,
                  generation: {
                    status: 'done',
                    video: result.video,
                    cover: result.cover,
                    lastFrameUrl: result.lastFrameUrl,
                  },
                },
              }
            : f,
        ),
      )
      setPreviewVersionId(null)
      setStatus(translateUi("workflow.videoVersionSwitched"))
    } catch (err) {
      if (!autosave.isActive()) return
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSwitchVideoVersions"))
    } finally {
      if (autosave.isActive()) setBusy(false)
    }
  }

  // 批量生成：入队本集分镜（forceRegen 覆盖已有成片；入口仅 MiniMax-H3）
  async function generateAll(opts?: { forceRegen?: boolean; skipConfirm?: boolean }) {
    if (fragments.length === 0) {
      setError(translateUi("workflow.noStoryboardShotsAvailableToGenerate"))
      return
    }
    if (generateAllLocked) {
      setError(translateUi("workflow.submittingPleaseWait"))
      return
    }

    const plannedRows = autosave.rows()
    const allBlocking: string[] = []
    const allWarnings: string[] = []
    let missingReference = false
    const doneIndices: number[] = []
    for (let i = 0; i < plannedRows.length; i++) {
      if ((plannedRows[i].id && generatingIds.has(plannedRows[i].id!)) ||
          (!opts?.forceRegen && shouldSkipGenerateAllFragment(plannedRows[i]))) {
        doneIndices.push(i)
        continue
      }
      const { blocking, warnings } = collectDramaGenerateGateIssues(plannedRows[i], assets)
      if (blocking.some((issue) => issue.code === 'missing_reference_image')) missingReference = true
      const label = formatFragLabel(i, plannedRows[i]?.duration_sec)
      for (const issue of blocking) allBlocking.push(`${label}：${issue.message}`)
      for (const issue of warnings) allWarnings.push(`${label}：${issue.message}`)
    }
    const pendingCount = plannedRows.length - doneIndices.length
    const pendingKeys = new Set(plannedRows.filter((_, index) => !doneIndices.includes(index)).map((row) => row.clientKey))
    if (pendingCount <= 0) {
      setStatus(translateUi("workflow.allStoryboardClipsInThisEpisodeAreCompleteAnd"))
      return
    }
    if (allBlocking.length > 0) {
      setError(allBlocking[0] || translateUi("workflow.storyboardScriptValidationFailed"))
      const message = [translateUi("workflow.fixTheseIssuesFirst"), '', ...allBlocking.slice(0, 8).map((m) => `· ${m}`)].join('\n')
      if (missingReference) {
        const go = await dialog.confirm({
          title: translateUi("workflow.addTheMissingReferenceImagesFirst"), message: translateUi("workflow.openTheAssetLibraryAndChooseGenerateMissingImages", { v1: String(message) }),
          confirmText: translateUi("workflow.generateMissingImages_df3aa9"),
        })
        if (go) await openReferenceAssets()
      } else {
        await dialog.alert({ title: translateUi("workflow.cannotGenerateThisBatch"), message })
      }
      return
    }

    setBusy(true)
    setError('')
    setStatus(translateUi("workflow.savingAndEstimatingStoryboardClips", { v1: String(pendingCount) }))
    const selectedModelId = modelId
    try {
      const ep = await save()
      setBusy(true)
      const ids = autosave.rows()
        .filter((row) => pendingKeys.has(row.clientKey))
        .map((f) => f.id)
        .filter((id): id is number => typeof id === 'number' && id > 0)
      if (ids.length === 0) {
        setStatus(translateUi("workflow.afterSavingAllStoryboardClipsWereAlreadyCompleteAnd"))
        setBusy(false)
        return
      }
      const quote = await dramaApi.quoteEpisodeVideos(eid, ids, selectedModelId)
      if (!quote.count) {
        setStatus(translateUi("workflow.theSelectedStoryboardClipsAreAlreadyGeneratingNoNeed"))
        setBusy(false)
        return
      }
      if (!opts?.skipConfirm || includeUpdatedImages) {
        const updatedCount = (ep.fragments || []).filter((f) => quote.fragment_ids.includes(f.id!) &&
          fragmentReferenceFreshness(f, assets) === 'image_updated').length
        const ok = await dialog.confirm({
          title: opts?.forceRegen ? translateUi("workflow.regenerateWithNewOutputSettings") : translateUi("workflow.generateStoryboardClipsInBulk"),
          message: formatDramaGateMessage([], allWarnings.slice(0, 8).map((message) => ({ level: 'warn' as const, message })),
            translateUi("workflow.queueClipsEstimatedSettledByActualUsageExistingClips", { v1: String(quote.count), v2: String(updatedCount ? translateUi("workflow.includingRemadeForUpdatedReferenceImages", { v1: String(updatedCount) }) : ''), v3: String((quote.estimate_fen / 100).toFixed(2)), v4: String(linkLastFrame ? translateUi("workflow.eachShotWaitsForThePreviousLastFrame") : '') })),
          confirmText: translateUi("workflow.confirmAndQueue"), tone: 'danger',
        })
        if (!ok) { setBusy(false); setStatus(translateUi("workflow.savedWithoutSubmittingGeneration")); return }
      }
      const genResult = await dramaApi.generateEpisode(eid, quote.fragment_ids, selectedModelId, quote.fragments)
      const queuedIds =
        Array.isArray(genResult.fragment_ids) && genResult.fragment_ids.length > 0
          ? genResult.fragment_ids
          : ids
      enqueueEpisodeVideoJobs({
        projectId: pid,
        episodeId: eid,
        episodeName: ep.name || episode?.name,
        fragments: (ep.fragments || []).map((f, i) => ({
          id: f.id,
          sort_order: f.sort_order ?? i,
        })),
        fragmentIds: queuedIds,
      })
      const deferred = Number(genResult.deferred_count || 0)
      const limit = Number(genResult.user_job_limit || 0)
      if (deferred > 0 && limit > 0) {
        setStatus(
          translateUi("workflow.queuedClipsUpToConcurrentWithWaiting", { v1: String(queuedIds.length), v2: String(limit), v3: String(deferred) }),
        )
      } else {
        setStatus(translateUi("workflow.queuedClipsGenerating", { v1: String(queuedIds.length) }))
      }
      setBusy(false)
    } catch (err) {
      if (!autosave.isActive()) return
      setError(err instanceof Error ? err.message : translateUi("workflow.batchGenerationFailed"))
      setBusy(false)
    }
  }

  // 返回剧情大纲
  async function handleBack() {
    try { await save(); navigate(`/drama/projects/${pid}`, { state: { activeStep: 'outline' } }) } catch { /* 保留本地稿 */ }
  }

  // 左侧目录切换分集
  async function handleSelectEpisode(nextId: number) {
    if (nextId === eid) return
    try { await save(); navigate(`/drama/projects/${pid}/episodes/${nextId}`) } catch { /* 保留本地稿 */ }
  }

  // 单集 LLM 重新分镜：确认并勾选 Skill 后入队
  function planFragmentsWithLlm() {
    if (planFragmentsLocked) {
      setError(translateUi("workflow.waitForActiveVideoJobsToFinishBeforeReplanning"))
      return
    }
    setPlanModalOpen(true)
  }

  // 入队后轮询至完成（字幕方式沿用顶栏当前设置）
  async function startPlanFragments(skillIds: number[]) {
    setPlanModalOpen(false)
    setBusy(true)
    setError('')
    setStatus(translateUi("workflow.waitingForGeneration"))
    try {
      await save()
      const planEpoch = autosave.epoch()
      await dramaApi.planEpisodeFragments(eid, {
        force: true,
        fallback_rules: true,
        skill_ids: skillIds,
        subtitle_enabled: subtitleModeUsesModelOutput(subtitleMode),
      })
      const started = Date.now()
      while (Date.now() - started < 10 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 2500))
        const ep = await dramaApi.getEpisode(eid)
        if (!autosave.isActive()) return
        const st = readFragmentPlanStatus(ep)
        if (st === 'completed') {
          if (!autosave.isActive()) return
          if (autosave.state().dirty || autosave.epoch() !== planEpoch) {
            setError(translateUi("workflow.theStoryboardHasChangedYourLocalDraftIsKept"))
            setBusy(false)
            return
          }
          rememberVideoBaselines(ep.fragments || [], true)
          autosave.load(ep)
          setEpisode(ep)
          const nextParams = (ep.params as Record<string, unknown>) || {}
          setEpisodeParams(nextParams)
          episodeParamsRef.current = nextParams
          setSelectedIndex(0)
          setEditing(false)
          const mode = String(ep.params?.fragment_plan_mode || 'llm')
          const count = Number(ep.params?.fragment_plan_count) || (ep.fragments || []).length
          setStatus(
            mode === 'rules_fallback'
              ? translateUi("workflow.storyboardCompleteRuleBasedFallbackAfterModelFailureClips", { v1: String(count) })
              : translateUi("workflow.storyboardCompleteClips", { v1: String(count) }),
          )
          setBusy(false)
          return
        }
        if (st === 'failed') {
          const msg = String(ep.params?.fragment_plan_error || translateUi("workflow.aiStoryboardPlanningFailed"))
          setError(msg)
          setBusy(false)
          return
        }
        setStatus(translateUi("workflow.waitingForGeneration"))
      }
      throw new Error(translateUi("workflow.aiStoryboardPlanningTimedOutRefreshLaterToCheck"))
    } catch (err) {
      if (!autosave.isActive()) return
      setError(err instanceof Error ? err.message : translateUi("workflow.aiStoryboardPlanningFailed"))
      setBusy(false)
    }
  }

  // 点击资产插入 @ 引用
  function mentionAsset(asset: DramaAsset) {
    if (!selected) return
    const mention = `@asset:${asset.id}`
    const raw = selected.content || ''
    const already = new RegExp(`@asset:${asset.id}(?!\\d)`).test(raw)
    const content = already ? raw : raw ? `${raw.trimEnd()}\n${mention}` : mention
    const ids = Array.from(new Set([...(selected.asset_ids || []), asset.id]))
    updateSelected({ content, asset_ids: ids })
    setEditing(true)
  }

  // 解析侧栏当前要新建的资产类型（未选分类时默认角色）
  function resolveCreateAssetTab(): AssetTab {
    return assetTab || 'character'
  }

  // 新建后挂到当前分镜并打开详情，便于本集列表立刻可见
  function adoptCreatedAsset(created: DramaAsset, kind: AssetTab) {
    setAssets((prev) => (prev.some((a) => a.id === created.id) ? prev : [...prev, created]))
    setAssetTab(kind)
    mentionAsset(created)
    if ((created.type || '').toLowerCase() !== 'voice') {
      setDetailAsset(created)
    }
  }

  // 自定义新建角色 / 场景 / 道具
  async function handleCreateSideAsset() {
    const kind = resolveCreateAssetTab()
    const label = kind === 'scene' ? translateUi("workflow.scene") : kind === 'prop' ? translateUi("workflow.prop") : translateUi("workflow.character")
    const name = await dialog.prompt({
      title: translateUi("workflow.create_820804", { v1: String(label) }),
      message: translateUi("workflow.enterANameForTheItWillBeInserted", { v1: String(label) }),
      placeholder: kind === 'scene' ? translateUi("workflow.forExamplePalaceGarden") : kind === 'prop' ? translateUi("workflow.forExampleJadePendant") : translateUi("workflow.forExampleWhiteDragon"),
      confirmText: translateUi("workflow.create_fcbd09"),
    })
    if (!name?.trim()) return
    setAssetCreateBusy(true)
    setError('')
    try {
      const created = await dramaApi.createAsset({
        project_id: pid,
        type: kind,
        asset_type: 'image',
        name: name.trim(),
        params: { kind },
      })
      adoptCreatedAsset(created, kind)
      setStatus(translateUi("workflow.createdAndReferencedItInThisStoryboardShot", { v1: String(label), v2: String(created.name) }))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotCreate_05a33d", { v1: String(label) }))
    } finally {
      setAssetCreateBusy(false)
    }
  }

  // 从全局资产库导入到本项目并挂到当前分镜
  async function handleImportSideAsset(source: DramaAsset) {
    const kind = normalizeAssetTab(source.type) || resolveCreateAssetTab()
    setAssetCreateBusy(true)
    setError('')
    try {
      const created = await importGlobalAssetToProject(pid, source)
      adoptCreatedAsset(created, kind)
      setLibraryPickerOpen(false)
      setStatus(translateUi("workflow.importedAndReferencedItInThisStoryboardShot", { v1: String(created.name) }))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotImportTheAsset"))
      throw err
    } finally {
      setAssetCreateBusy(false)
    }
  }

  // 仅取消当前分镜对该资产的关联（不删除项目资产）
  function unlinkSelectedAsset(assetId: number) {
    if (!selected) return
    const content = (selected.content || '')
      .replace(new RegExp(`\\s*@asset:${assetId}(?!\\d)`, 'g'), ' ')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
    const asset_ids = (selected.asset_ids || []).filter((id) => id !== assetId)
    updateSelected({ content, asset_ids })
    setEditing(true)
    const name = assets.find((a) => a.id === assetId)?.name
    setStatus(
      name
        ? translateUi("workflow.unlinkedFromThisShotItRemainsInTheProject", { v1: String(name) })
        : translateUi("workflow.unlinkedFromThisShotTheAssetRemainsInThe"),
    )
  }

  // 从关联条跳到对应分类并打开资产详情
  function focusLinkedAsset(assetId: number) {
    const asset = assets.find((a) => a.id === assetId)
    if (!asset) return
    const tab = normalizeAssetTab(asset.type)
    if (tab) setAssetTab(tab)
    setAssetScope('series')
    if ((asset.type || '').toLowerCase() !== 'voice') {
      setDetailAsset(asset)
    }
  }

  // 更新资产（音色绑定 / 上传 / 生图后刷新列表与详情）
  function handleCharacterUpdated(updated: DramaAsset) {
    setAssets((prev) => prev.map((a) => (a.id === updated.id ? updated : a)))
    setDetailAsset((prev) => (prev?.id === updated.id ? updated : prev))
  }

  // 当前排队/生成中的资产生图 id
  const imageBusyIds = useMemo(() => {
    const ids = new Set<number>()
    for (const job of imageGenQueue) {
      if (job.status === 'queued' || job.status === 'running') ids.add(job.assetId)
    }
    return ids
  }, [imageGenQueue])

  // 详情弹窗生图按钮文案
  function assetImageGenLabel(asset: DramaAsset): string {
    const job = imageGenQueue.find(
      (j) =>
        j.assetId === asset.id && (j.status === 'queued' || j.status === 'running'),
    )
    let queueLabel: string | null = null
    if (job) {
      if (job.status === 'running') queueLabel = translateUi("workflow.generating")
      else {
        const queuedOnly = imageGenQueue.filter(
          (j) => j.status === 'queued' || j.status === 'running',
        )
        const pos = queuedOnly.findIndex((j) => j.id === job.id) + 1
        queueLabel = pos > 1 ? translateUi("workflow.queued_381f95", { v1: pos }) : translateUi("workflow.queued_f62e20")
      }
    }
    return dramaAssetImageGenButtonLabel(asset, queueLabel)
  }

  // 将资产生图加入全局队列
  function enqueueAssetImage(asset: DramaAsset) {
    if (imageBusyIds.has(asset.id)) return
    const options = {
      ...defaultOptionsForAssetKind(asset.type),
      image_style_id: videoStyleId || undefined,
    }
    void enqueueDramaImageGen({
      projectId: pid,
      assetId: asset.id,
      assetName: asset.name || undefined,
      assetType: asset.type,
      prompt: readVisualPrompt(asset),
      options,
      onAssetUpdate: handleCharacterUpdated,
    })
      .then((updated) => handleCharacterUpdated(updated))
      .catch((err) => setError(err instanceof Error ? err.message : translateUi("workflow.imageGenerationFailed")))
  }

  // 打开分镜失败原因（优先队列任务，否则用分镜 params.generation.error）
  function openFragmentFailReason(frag: DramaFragment, index: number) {
    if (!frag.id) return
    const fromQueue = dramaGenQueue.find(
      (j) => j.id === videoJobId(frag.id!) || (j.kind === 'video' && j.targetId === frag.id),
    )
    const gen = readFragmentGenerationStatus(frag)
    const title = `${episode?.name || translateUi("workflow.thisEpisode")} · ${formatFragLabel(index, frag.duration_sec)}`
    setFailReasonJob({
      id: fromQueue?.id || videoJobId(frag.id),
      kind: 'video',
      projectId: pid,
      targetId: frag.id,
      episodeId: eid || undefined,
      taskId: fromQueue?.taskId,
      title: fromQueue?.title || title,
      subtype: translateUi("workflow.storyboardVideo"),
      status: 'failed',
      error: fromQueue?.error || gen.error || translateUi("ecomSet.result.failed"),
      createdAt: fromQueue?.createdAt || Date.now(),
    })
  }

  // 一键 AI 生成角色音色并绑定（各角色独立 busy，互不阻塞）
  async function handleGenerateCharacterVoice(asset: DramaAsset) {
    if (characterVoiceBusyIds.has(asset.id)) return
    setCharacterVoiceBusyIds((prev) => new Set(prev).add(asset.id))
    setError('')
    try {
      const { character, voice } = await generateAndBindCharacterVoice(pid, asset)
      handleCharacterUpdated(character)
      setAssets((prev) => (prev.some((a) => a.id === voice.id) ? prev : [...prev, voice]))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.voiceGenerationFailed"))
    } finally {
      setCharacterVoiceBusyIds((prev) => {
        const next = new Set(prev)
        next.delete(asset.id)
        return next
      })
    }
  }

  if (!episode) {
    return (
      <div className="drama-ep-fullscreen drama-ep-center">
        {error || translateUi("ecomSet.loading")}
      </div>
    )
  }

  // 当前预览/选中分镜 id（与底部分镜条、脚本编辑联动）
  const playingFragmentId = selected?.id ?? null

  // 切换预览分镜时同步底部分镜选中态
  function handlePlayingFragmentChange(fragmentId: number) {
    const index = fragments.findIndex((f) => f.id === fragmentId)
    if (index >= 0) {
      setSelectedIndex(index)
    }
  }

  return (
    <div className="drama-ep-fullscreen">
      <header className="drama-ep-header">
        <div className="drama-ep-header-left">
          <button type="button" className="drama-ep-icon-btn" aria-label={translateUi("workflow.back_11d024")} onClick={handleBack}>
            ‹
          </button>
          <h1>{episode.name}</h1>
        </div>
        <div className="drama-ep-header-controls">
          <MyTasksButton />
          <EpisodeEditHeaderControls
            styleId={videoStyleId}
            modelId={modelId}
            episodeParams={episodeParams}
            projectParams={projectParams}
            linkLastFrame={linkLastFrame}
            subtitleMode={subtitleMode}
            characterIntroMode={characterIntroMode}
            onStyleChange={setVideoStyleId}
            onModelChange={setModelId}
            onEpisodeOutputChange={handleEpisodeOutputChange}
            onLinkLastFrameChange={(enabled) => void handleLinkLastFrameChange(enabled)}
            onSubtitleModeChange={(mode) => void handleEpisodeSubtitleChange(mode)}
            onCharacterIntroModeChange={(mode) => void handleEpisodeCharacterIntroChange(mode)}
            disabled={busy}
            globalSettingsReadOnly
          />
          <button
            type="button"
            className="drama-ep-btn-ghost"
            disabled={planFragmentsLocked}
            onClick={() => void planFragmentsWithLlm()}
          >
            {busy && (status.includes(translateUi("workflow.storyboard")) || status.includes(translateUi("workflow.waitingForGeneration_436346")))
              ? translateUi("workflow.waitingForGeneration")
              : translateUi("workflow.replanStoryboardsWithAi")}
          </button>
          {/* MiniMax-H3 支持一次入队本集剩余分镜；其它视频模型仍只走单镜生成 */}
          {isMinimaxH3Model(modelId) ? (
            <>
            <label><input type="checkbox" checked={includeUpdatedImages} disabled={busy}
              onChange={(event) => setIncludeUpdatedImages(event.target.checked)} />{translateUi("workflow.includeShotsWithUpdatedImages")}</label>
            <button
              type="button"
              className="drama-ep-btn-dark drama-ep-header-gen-all"
              disabled={generateAllLocked || fragments.length === 0}
              onClick={() => void generateAll()}
            >
              {busy ? translateUi("workflow.queuing") : translateUi("ecomSet.steps.2")}
            </button>
            </>
          ) : null}
        </div>
      </header>

      <p role="status">{saveState.conflict ? translateUi("workflow.saveConflictYourLocalDraftIsKeptRefreshAnd") : saveState.saving ? translateUi("workflow.saving") : saveState.dirty ? translateUi("ecomSet.plan.unsaved") : translateUi("ecomSet.plan.saved")}</p>
      {(status || error) && (
        <div className="drama-ep-banner">
          {error ? <BillingErrorNotice message={error} className="drama-ep-banner-error" inline /> : null}
          {!error && status ? <span>{status}</span> : null}
        </div>
      )}

      <div className="drama-ep-body">
        <DramaEpisodeDir
          items={buildEpisodeDirItems(episodeList.length ? episodeList : episode ? [episode] : [])}
          activeId={eid}
          onSelect={handleSelectEpisode}
        />
        <div className="drama-ep-workspace">
        <EpisodeEditAssetPanel
          scope={assetScope}
          tab={assetTab}
          assets={filteredAssets}
          activeIds={selectedRefIds}
          imageBusyIds={imageBusyIds}
          createBusy={assetCreateBusy}
          onScopeChange={setAssetScope}
          onTabChange={setAssetTab}
          onOpenCanvas={openEpisodeStoryboard}
          onOpenAsset={setDetailAsset}
          onMention={mentionAsset}
          onUnlinkAsset={unlinkSelectedAsset}
          onGenerateVoice={
            DRAMA_VOICE_BINDING_ENABLED
              ? (asset) => void handleGenerateCharacterVoice(asset)
              : undefined
          }
          voiceBusyIds={characterVoiceBusyIds}
          onVoiceError={(message) => setError(message)}
          onCreateAsset={() => void handleCreateSideAsset()}
          onImportAsset={() => setLibraryPickerOpen(true)}
        />

        <section className="drama-ep-editor">
          <div className="drama-ep-editor-head">
            <div>
              <strong>{formatFragLabel(selectedIndex, selectedDuration)}</strong>
              <p>
                {translateUi("workflow.referencesAppearAboveTypeToAddAnAssetOr", { v1: ' ', v2: formatProjectOutputLabel(
                  readEpisodeAspectRatio(episodeParams, projectParams),
                  readEpisodeResolution(episodeParams, projectParams),
                ) })}</p>
            </div>
            <label className="drama-ep-duration">
              {translateUi("workflow.duration")}
              <input
                type="number"
                min={4}
                max={15}
                value={selectedDuration}
                disabled={!editing && !selected}
                onChange={(e) =>
                  updateSelected({ duration_sec: Number(e.target.value) || 8 })
                }
              />
              s
            </label>
          </div>

          <EpisodeEditReferenceStrip items={selectedRefItems} onSelect={focusLinkedAsset}
            onMoveCharacter={moveCharacterReference} disabled={busy} />
          {selected && fragmentReferenceStatusLabel(selected, assets) ?
            <p role="status">{fragmentReferenceStatusLabel(selected, assets)}</p> : null}

          <div className={`drama-ep-editor-box ${editing ? 'editing' : ''}`}>
            <EpisodeEditPromptEditor
              content={selected?.content || ''}
              assets={assets}
              referencedIds={referencedIds}
              editing={editing}
              onOpenAsset={focusLinkedAsset}
              onContentChange={(nextContent) => {
                const fromContent = extractAssetIds(nextContent)
                const prevContentIds = extractAssetIds(selected?.content || '')
                const removed = prevContentIds.filter((id) => !fromContent.includes(id))
                /* 正文删掉的引用同步移出 asset_ids；仅存在于 asset_ids 的额外关联保留 */
                const prevContentIdSet = new Set(prevContentIds)
                const keptExtra = (selected?.asset_ids || []).filter(
                  (id) => !prevContentIdSet.has(id) || fromContent.includes(id),
                )
                updateSelected({
                  content: nextContent,
                  asset_ids: Array.from(new Set([...keptExtra, ...fromContent])),
                })
                if (removed.length > 0) {
                  setStatus(translateUi("workflow.unlinkedFromThisShotTheAssetRemainsInThe_bbd4b8"))
                }
              }}
            />

          {selectedGateIssues.length > 0 ? (
            <ul className="drama-ep-script-issues" aria-live="polite">
              {selectedGateIssues.map((issue) => (
                <li
                  key={`${issue.level}:${issue.message}`}
                  className={
                    issue.level === 'error'
                      ? 'drama-ep-script-issue is-error'
                      : 'drama-ep-script-issue is-warn'
                  }
                >
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : null}

          {linkLastFrame && selectedIndex > 0 ? (
            <p
              className={`drama-ep-continuity-hint${
                !continuityBlockedReason ? ' is-ready' : ' is-wait'
              }`}
            >
              {continuityBlockedReason
                ? translateUi("workflow.lastFrameContinuityEnabled", { v1: String(continuityBlockedReason) })
                : continuityQueueHint
                  ? continuityQueueHint
                  : prevLastFrameUrl
                    ? translateUi("workflow.usesThePreviousShotSLastFrameAlongsideCharacter")
                    : translateUi("workflow.thePreviousClipIsCompleteItsLastFrameWill")}
            </p>
          ) : !linkLastFrame ? (
            <p className="drama-ep-continuity-hint">
              {translateUi("workflow.lastFrameContinuityIsOffStoryboardClipsGenerateIndependently")}
            </p>
          ) : null}

          <div className="drama-ep-editor-actions">
            {selectedGateBlocking.some((issue) => issue.code === 'missing_reference_image') ? (
              <button
                type="button"
                className="drama-ep-btn-ghost"
                disabled={busy}
                onClick={() => void openReferenceAssets()}
              >
                {translateUi("workflow.generateMissingReferenceImages")}
              </button>
            ) : null}
            {editing ? (
              <>
                <button
                  type="button"
                  className="drama-ep-btn-ghost"
                  disabled={busy}
                  onClick={() => { void save().then(() => { if (autosave.isActive()) setEditing(false) }).catch(() => undefined) }}
                >
                  {translateUi("workflow.finishEditing")}
                </button>
                <button
                  type="button"
                  className="drama-ep-btn-dark"
                  disabled={busy}
                  onClick={() => void save().catch(() => undefined)}
                >
                  {busy ? translateUi("workflow.saving") : translateUi("workflow.save")}
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="drama-ep-btn-ghost"
                  disabled={!selected || selectedIsGenerating || busy}
                  onClick={() => setEditing(true)}
                >
                  {translateUi("workflow.edit")}
                </button>
                <button
                  type="button"
                  className="drama-ep-btn-dark"
                  disabled={selectedGenerateLocked || !selected}
                  title={
                    selectedGateBlocking[0]?.message ||
                    continuityBlockedReason ||
                    continuityQueueHint ||
                    (selectedIsGenerating ? translateUi("workflow.thisStoryboardShotIsGenerating") : undefined)
                  }
                  onClick={() => void generateSelected()}
                >
                  {selectedIsGenerating
                    ? translateUi("workflow.generating")
                    : busy
                      ? translateUi("workflow.processing_1cac8a")
                      : selectedGateBlocking.length > 0
                        ? translateUi("workflow.addReferenceImagesFirst")
                        : continuityQueueHint
                          ? translateUi("workflow.queueGeneration")
                          : continuityBlockedReason
                            ? translateUi("workflow.waitingForPreviousShot")
                            : selectedHasVideo
                              ? translateUi("ecomSet.result.regenerate")
                              : translateUi("workflow.generate")}
                </button>
              </>
            )}
          </div>
          </div>

          {selectedVersions.length > 0 && selected?.id ? (
            <div className="drama-ep-versions">
              <span className="drama-ep-versions-label">{translateUi("workflow.versionHistory")}</span>
              <div className="drama-ep-versions-list">
                {selectedHasVideo && selected.video ? (
                  <button
                    type="button"
                    className={`drama-ep-version is-active${!previewVersionId ? ' is-current' : ''}`}
                    disabled={busy || selectedIsGenerating}
                    title={translateUi("workflow.currentVideo")}
                    onClick={() => setPreviewVersionId(null)}
                  >
                    {selected.cover ? (
                      <img src={resolveDramaMediaUrl(selected.cover)} alt="" />
                    ) : (
                      <video src={resolveDramaMediaUrl(selected.video)} muted />
                    )}
                    <em>{translateUi("ecomSet.history.current")}</em>
                  </button>
                ) : null}
                {selectedVersions.map((ver, index) => {
                  const cover = ver.cover ? resolveDramaMediaUrl(ver.cover) : ''
                  const video = resolveDramaMediaUrl(ver.video)
                  const versionNo = selectedVersions.length - index
                  return (
                    <button
                      key={ver.id}
                      type="button"
                      className={`drama-ep-version${previewVersionId === ver.id ? ' is-previewing' : ''}`}
                      disabled={busy || selectedIsGenerating}
                      title={translateUi("workflow.clickToPreviewUseTheRightPanelToMake")}
                      onClick={() => setPreviewVersionId(ver.id)}
                    >
                      {cover ? <img src={cover} alt="" /> : <video src={video} muted />}
                      <em>v{versionNo}</em>
                    </button>
                  )
                })}
              </div>
            </div>
          ) : null}
        </section>

        <EpisodeEditSidePane
          fragments={fragments}
          playingFragmentId={playingFragmentId}
          onPlayingFragmentChange={handlePlayingFragmentChange}
          aspectRatio={aspectRatio}
          episodeId={episode?.id}
          episodeName={episode?.name || translateUi("workflow.thisEpisode")}
          subtitleMode={subtitleMode}
          onOpenStoryboard={openEpisodeStoryboard}
          previewVideoUrl={previewVideoUrl}
          previewPosterUrl={previewPosterUrl}
          previewLabel={
            previewVersion
              ? `v${
                  selectedVersions.length -
                  selectedVersions.findIndex((ver) => ver.id === previewVersion.id)
                }`
              : ''
          }
          onClearPreview={() => setPreviewVersionId(null)}
          onActivatePreview={
            previewVersionId
              ? () => {
                  void activateVideoVersion(previewVersionId)
                }
              : undefined
          }
        />
        </div>
      </div>

      <footer className="drama-ep-storyboard">
        <ComplianceNotice
          fetchReport={() => getEpisodeCompliance(eid)}
          refreshKey={complianceKey}
          applyDisabled={busy || saveState.conflict}
          onApply={async (item, hit) => {
            const current = autosave.rows().find((row) => row.id === item.fragment_id)
            if (!current) throw new Error(translateUi("workflow.theTextHasChangedRunTheCheckAgainBefore"))
            const content = applyComplianceSuggestion(current.content, item.text, hit)
            editFragments((rows) => rows.map((row) => row.clientKey === current.clientKey ? { ...row, content } : row))
            await save()
          }}
          describe={(item) => {
            const pos = fragments.findIndex((f) => f.id === item.fragment_id)
            return {
              label: dramaComplianceLabel(item, fragments, t),
              onLocate: pos >= 0 ? () => setSelectedIndex(pos) : undefined,
            }
          }}
        />
        <div className="drama-ep-storyboard-row">
          <button
            type="button"
            className="drama-ep-insert"
            aria-label={translateUi("workflow.insertStoryboardShotAtTheStart")}
            onClick={() => insertFrag(0)}
          >
            +
          </button>
          {fragments.map((frag, index) => {
            const genInfo = readFragmentGenerationStatus(frag)
            const fragStatus = genInfo.status
            const activeVideoTask = (episode?.active_tasks || []).find(
              (task) =>
                task.task_type === 'fragment_video' &&
                task.fragment_id === frag.id &&
                !task.cancel_requested &&
                ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(
                  task.status,
                ),
            )
            // 终态优先；仅在进行中时才用平台任务态覆盖（避免 done 仍被僵尸任务刷成生成中）
            const displayStatus =
              fragStatus === 'done' ||
              fragStatus === 'failed' ||
              fragStatus === 'cancelled'
                ? fragStatus
                : activeVideoTask
                  ? activeVideoTask.status === 'pending' || activeVideoTask.status === 'leased'
                    ? 'queued'
                    : 'running'
                  : fragStatus
            const fragBusy =
              Boolean(frag.id && generatingIds.has(frag.id)) &&
              displayStatus !== 'done' &&
              displayStatus !== 'failed' &&
              displayStatus !== 'cancelled'
            const badge = fragmentQueueBadgeLabel(displayStatus)
            const clipVideo = frag.video ? resolveDramaMediaUrl(frag.video) : ''
            const clipCover = frag.cover ? resolveDramaMediaUrl(frag.cover) : ''
            const showFailHint = displayStatus === 'failed' && !fragBusy
            const fragParams =
              frag.params && typeof frag.params === 'object' && !Array.isArray(frag.params)
                ? (frag.params as Record<string, unknown>)
                : {}
            return (
            <div key={`${frag.id}-${index}`} className="drama-ep-clip-wrap">
              <div
                className={`drama-ep-clip-shell ${selectedIndex === index ? 'active' : ''}${
                  fragBusy ? ' is-generating' : ''
                }${displayStatus === 'queued' ? ' is-queued' : ''}${
                  displayStatus === 'failed' ? ' is-failed' : ''
                }`}
              >
                <button
                  type="button"
                  className="drama-ep-clip"
                  onClick={() => setSelectedIndex(index)}
                >
                  {clipCover ? (
                    <img src={clipCover} alt="" />
                  ) : clipVideo ? (
                    <video src={clipVideo} muted />
                  ) : (
                    <span className="drama-ep-clip-empty">
                      {fragBusy ? '…' : showFailHint ? (
                        <CircleAlert size={22} strokeWidth={2} aria-hidden />
                      ) : (
                        '+'
                      )}
                    </span>
                  )}
                  {badge ? <span className="drama-ep-clip-badge">{badge}</span> : null}
                  {!badge && fragmentReferenceStatusLabel(frag, assets) ? <span className="drama-ep-clip-badge">{fragmentReferenceStatusLabel(frag, assets)}</span> : null}
                  <em>
                    {formatFragLabel(index, frag.duration_sec)}
                    <DramaFragmentClipSpec
                      fragmentParams={fragParams}
                      episodeParams={episodeParams}
                      projectParams={projectParams}
                      videoUrl={clipVideo}
                    />
                  </em>
                </button>
                {showFailHint ? (
                  <button
                    type="button"
                    className="drama-ep-clip-fail-btn"
                    title={translateUi("workflow.viewFailureReason")}
                    aria-label={translateUi("workflow.viewFailureReasonForClip", { v1: String(index + 1) })}
                    onClick={() => openFragmentFailReason(frag, index)}
                  >
                    <CircleAlert size={14} strokeWidth={2.25} aria-hidden />
                  </button>
                ) : null}
              </div>
              <div className="drama-ep-clip-ops">
                <button type="button" aria-label={translateUi("workflow.insert")} onClick={() => insertFrag(index + 1)} disabled={busy}>
                  +
                </button>
                <button type="button" aria-label={translateUi("workflow.duplicate")} onClick={() => duplicateFrag(index)} disabled={busy}>
                  ⧉
                </button>
                <button
                  type="button"
                  aria-label={translateUi("workflow.delete")}
                  disabled={busy || fragments.length <= 1}
                  onClick={() => deleteFrag(index)}
                >
                  ⌫
                </button>
              </div>
            </div>
            )
          })}
        </div>
      </footer>

      <FragmentPlanSkillModal
        open={planModalOpen}
        message={translateUi("workflow.aiWillReplanThisEpisodeSStoryboardReplacingExisting")}
        onCancel={() => setPlanModalOpen(false)}
        onConfirm={(skillIds) => void startPlanFragments(skillIds)}
      />

      {detailAsset && (detailAsset.type || '').toLowerCase() !== 'voice' ? (
        <DramaAssetDetailModal
          asset={detailAsset}
          open
          busy={imageBusyIds.has(detailAsset.id)}
          genLabel={assetImageGenLabel(detailAsset)}
          onClose={() => setDetailAsset(null)}
          onUpdated={handleCharacterUpdated}
          onGenerate={(a) => enqueueAssetImage(a)}
          onBindVoice={DRAMA_VOICE_BINDING_ENABLED ? (a) => setVoiceBindAsset(a) : undefined}
          onError={(message) => setError(message)}
        />
      ) : null}

      {DRAMA_VOICE_BINDING_ENABLED && voiceBindAsset ? (
        <CharacterVoiceBindModal
          asset={voiceBindAsset}
          projectId={pid}
          open
          onClose={() => setVoiceBindAsset(null)}
          onBound={(updated) => {
            handleCharacterUpdated(updated)
            setVoiceBindAsset(null)
          }}
          onError={(message) => setError(message)}
        />
      ) : null}

      <GlobalAssetPickerModal
        open={libraryPickerOpen}
        onClose={() => setLibraryPickerOpen(false)}
        projectId={pid}
        defaultTab={resolveCreateAssetTab()}
        allowedTypes={
          resolveCreateAssetTab() === 'prop'
            ? ['prop', 'material', 'none']
            : [resolveCreateAssetTab()]
        }
        title={translateUi("workflow.import_309ff5", { v1: String(resolveCreateAssetTab() === 'scene' ? translateUi("workflow.scene") : resolveCreateAssetTab() === 'prop' ? translateUi("workflow.prop") : translateUi("workflow.character")) })}
        confirmLabel={translateUi("workflow.importIntoThisEpisode")}
        onPick={handleImportSideAsset}
      />

      {failReasonJob ? (
        <div className="drama-ep-fail-reason-pop">
          <DramaGenTaskDetail job={failReasonJob} onClose={() => setFailReasonJob(null)} />
        </div>
      ) : null}
    </div>
  )
}
