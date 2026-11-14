import { isVideoSkipped } from './daihuoMotion'
import { translate as translateUi } from '../i18n/runtime'
import { getActiveLocale } from '../i18n/detect'
import { messages } from '../i18n/messages'
import { IMAGE_TEXT_STEPS, imageTextBoardStep } from './imageTextStoryboard'
import { kepuBillingPhase } from './kepuShotActions'
import type { KepuBillingPhase } from './kepuShotActions'

/** 线上新增文案的中英文切换（未进 i18n 词表的少量句子） */
function localeText(zh: string, en: string): string {
  return getActiveLocale() === 'en' ? en : zh
}

export const RUNNING = new Set([
  'SCRIPTING',
  'IMAGING',
  'VIDEOING',
  'AUDIOING',
  'COMPOSING',
  'AUDITING',
  'PARALLEL_ASSETS',
])

// 按当前界面语言取状态文案（兼容旧的 STATUS_CN[code] 写法）
export const STATUS_CN: Record<string, string> = new Proxy(
  {},
  {
    get(_target, prop: string) {
      const map = messages[getActiveLocale()].status as Record<string, string>
      return map[prop] || prop
    },
  },
)

export function isRunning(status: string) {
  return RUNNING.has(status)
}

export function hasActiveTasks(project: {
  active_tasks?: Array<{ status: string; cancel_requested?: boolean | null }> | null
}) {
  const activeStatuses = ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review']
  return Boolean(
    project.active_tasks?.some(
      (task) => !task.cancel_requested && activeStatuses.includes(task.status),
    ),
  )
}

/**
 * Prefer stage inferred from shot assets when status is still SCRIPTING
 * (e.g. continue-generate briefly labeled wrong, or worker lag).
 */
export function effectiveStatus(project: {
  status: string
  pipeline_mode?: string | null
  shots?: Array<{ image_url?: string | null; audio_url?: string | null; video_url?: string | null }>
}): string {
  const status = project.status
  const shots = project.shots || []
  if (status !== 'SCRIPTING' || shots.length === 0) return status

  const full = project.pipeline_mode !== 'image_text'
  const imgs = shots.filter((s) => s.image_url).length
  const auds = shots.filter((s) => s.audio_url).length
  const vids = shots.filter((s) => s.video_url).length
  const n = shots.length
  // full 管线配音由视频模型完成，不要求 TTS audio_url
  const assetsOk = full ? imgs === n : imgs === n && auds === n

  if (assetsOk) {
    if (full && vids < n) return 'VIDEOING'
    if (full && vids === n) return 'COMPOSING'
    if (!full) return 'COMPOSING'
  }
  if (imgs > 0 || auds > 0) return 'IMAGING'
  return status
}

export function statusLabel(projectOrStatus: string | Parameters<typeof effectiveStatus>[0]) {
  const status = typeof projectOrStatus === 'string' ? projectOrStatus : effectiveStatus(projectOrStatus)
  return STATUS_CN[status] || status
}

export function statusTone(status: string): 'ok' | 'bad' | 'run' | 'idle' {
  if (status === 'DONE') return 'ok'
  if (status === 'FAILED' || status === 'REJECTED' || status === 'CANCELLED') return 'bad'
  if (isRunning(status)) return 'run'
  return 'idle'
}

/** Per-shot statuses from pipeline */
export const SHOT_STATUS_CN: Record<string, string> = new Proxy(
  {},
  {
    get(_target, prop: string) {
      const map = messages[getActiveLocale()].shotStatus as Record<string, string>
      return map[prop] || STATUS_CN[prop] || prop
    },
  },
)

// 兼容旧调用：直接翻译 shot.status
export function shotStatusLabel(status: string) {
  return SHOT_STATUS_CN[status] || STATUS_CN[status] || status
}

// 旧「镜头终态」判断；分镜表请用 shotDisplayDone
export function shotIsDone(status: string) {
  return ['AUDIO_READY', 'VIDEO_READY', 'DONE', 'IMAGE_READY'].includes(status)
}

/** 分镜表按素材完备度展示，不盲信 shot.status（AUDIO_READY 只代表配音） */
export type ShotDisplayKind =
  | 'failed'
  | 'generating'
  | 'wait_image'
  | 'wait_video'
  | 'image_ready'
  | 'video_ready'

export type ShotAssetLike = {
  status?: string | null
  image_url?: string | null
  video_url?: string | null
  /** static=带货静图推拉；privacy=真人拦截后用静图 */
  video_skip_reason?: string | null
}

/*
 * SHOT_TASK_ACTIVE 任务中心进行中状态
 * PROJECT_WIDE_TASKS 整片占用，单镜生成需避开
 */
const SHOT_TASK_ACTIVE = [
  'pending',
  'leased',
  'running',
  'awaiting_poll',
  'awaiting_review',
  'cancel_requested',
]
const PROJECT_WIDE_TASKS = new Set([
  'project_pipeline',
  'project_compose_only',
  'project_regen_audio',
  'shot_regen_audio',
])

/** 按图/视频是否齐推断单镜展示态 */
export function shotDisplayKind(
  shot: ShotAssetLike,
  opts?: { pipelineMode?: string | null; generating?: boolean },
): ShotDisplayKind {
  if (opts?.generating) return 'generating'
  if (shot.status === 'FAILED') return 'failed'
  if (!shot.image_url) return 'wait_image'
  const full = opts?.pipelineMode !== 'image_text'
  if (full && !shot.video_url && isVideoSkipped(shot)) return 'image_ready'
  if (full && !shot.video_url) return 'wait_video'
  if (full) return 'video_ready'
  return 'image_ready'
}

/** 该镜素材是否已齐（静图看图，全流程看视频） */
export function shotDisplayDone(kind: ShotDisplayKind) {
  return kind === 'image_ready' || kind === 'video_ready'
}

/** 分镜表 / CSV 用的素材态文案 */
export function shotDisplayLabel(kind: ShotDisplayKind) {
  const key = {
    failed: 'FAILED',
    generating: 'GENERATING',
    wait_image: 'WAIT_IMAGE',
    wait_video: 'WAIT_VIDEO',
    image_ready: 'IMAGE_READY',
    video_ready: 'VIDEO_READY',
  }[kind]
  return SHOT_STATUS_CN[key] || key
}

type TaskLike = {
  status: string
  task_type?: string | null
  shot_id?: number | null
  cancel_requested?: boolean | null
}

/** 项目是否有进行中的工作：有任务平台数据时以 active_tasks 为准；无任务的运行状态视为失败残留，可重试。 */
export function isProjectBusy(project: { status: string; active_tasks?: TaskLike[] | null } | null): boolean {
  if (!project) return false
  const activeStatuses = ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review', 'cancel_requested']
  if (project.active_tasks?.some((task) => activeStatuses.includes(task.status))) return true
  if (Array.isArray(project.active_tasks) && project.active_tasks.length === 0) return false
  return isRunning(project.status)
}

/** 整片流水线 / 合成 / 整片配音进行中 */
export function isProjectWideBusy(project: {
  status?: string
  active_tasks?: TaskLike[] | null
}) {
  const tasks = project.active_tasks
  if (Array.isArray(tasks)) {
    return tasks.some(
      (task) =>
        SHOT_TASK_ACTIVE.includes(task.status) && PROJECT_WIDE_TASKS.has(task.task_type || ''),
    )
  }
  return isRunning(project.status || '')
}

/** 该镜是否有进行中的单镜任务 */
export function isShotGenerating(
  project: { active_tasks?: TaskLike[] | null } | null | undefined,
  shotId: number,
) {
  return Boolean(
    project?.active_tasks?.some(
      (task) => task.shot_id === shotId && SHOT_TASK_ACTIVE.includes(task.status),
    ),
  )
}

export function formatMmSs(seconds: number) {
  const s = Math.max(0, Math.round(seconds || 0))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

/** 科普全链路步骤：建项 / 风格 / 分镜台共用 */
export const KEPU_STEPS = [
  { key: 'topic', get label() { return translateUi("workflow.topic") } },
  { key: 'style', get label() { return translateUi("ecomSet.form.style") } },
  { key: 'confirm', get label() { return translateUi("workflow.confirmStoryboard") } },
  { key: 'assets', get label() { return localeText('参考图画面', 'Reference images') } },
  { key: 'videos', get label() { return localeText('镜头视频', 'Shot videos') } },
  { key: 'compose', get label() { return translateUi("workflow.composeAndPreview") } },
]

export const CREATE_STEPS = KEPU_STEPS
export const BOARD_STEPS = KEPU_STEPS

/** 成片方式的中文名 */
export function pipelineModeLabel(mode?: string | null) {
  if (mode === 'image_text') return translateUi("workflow.stillImageVideo")
  if (mode === 'avatar') return translateUi("workflow.talkingHeadNarration")
  return translateUi("workflow.aiVideo")
}

/** 静图成片跳过「镜头视频」步；数字人口播改两步的名称 */
export function kepuSteps(pipelineMode?: string | null) {
  if (pipelineMode === 'image_text') {
    return IMAGE_TEXT_STEPS
  }
  if (pipelineMode === 'avatar') {
    return KEPU_STEPS.map((s) =>
      s.key === 'assets' ? { ...s, label: translateUi("workflow.photoAndVoiceover") } : s.key === 'videos' ? { ...s, label: translateUi("workflow.talkingHeadVideo") } : s,
    )
  }
  return KEPU_STEPS
}

export type KepuWizardPage = 'create' | 'style' | 'board'

export { kepuBillingPhase }
export type { KepuBillingPhase }

export function shotsByNo<T extends { shot_no: number }>(shots: T[] | null | undefined): T[] {
  return [...(shots || [])].sort((a, b) => a.shot_no - b.shot_no)
}

/**
 * 当前步骤下标；分镜台跟 script / assets / videos / compose 对齐。
 */
export function kepuStepIndex(
  page: KepuWizardPage,
  project?: {
    status: string
    pipeline_mode?: string | null
    final_video_url?: string | null
    shots?: Array<{ image_url?: string | null; audio_url?: string | null; video_url?: string | null }>
  } | null,
): number {
  if (project?.pipeline_mode === 'image_text') {
    if (page === 'create' || page === 'style') return 0
    return imageTextBoardStep(project)
  }
  const steps = kepuSteps(project?.pipeline_mode)
  const idx = (key: string) => {
    const n = steps.findIndex((s) => s.key === key)
    return n >= 0 ? n : 0
  }
  if (page === 'create') return idx('topic')
  if (page === 'style') return idx('style')
  if (!project) return idx('confirm')
  const status = effectiveStatus(project)
  const shots = project.shots || []
  if (isRunning(status)) {
    if (status === 'VIDEOING') return idx('videos')
    if (['IMAGING', 'AUDIOING', 'PARALLEL_ASSETS'].includes(status)) return idx('assets')
    if (['COMPOSING', 'AUDITING'].includes(status)) return idx('compose')
  }
  if (project.final_video_url && status === 'DONE') return idx('compose')
  const phase = kepuBillingPhase(project)
  if (phase === 'compose') return idx('compose')
  if (phase === 'videos') return idx('videos')
  if (phase === 'assets') return idx('assets')
  if (status === 'DRAFT' && shots.length === 0) return idx('style')
  return idx('confirm')
}

/** 分镜台主按钮旁：本步做什么、是否预扣 */
export function kepuPhaseHint(project: {
  status: string
  pipeline_mode?: string | null
  shots?: Array<{ image_url?: string | null; audio_url?: string | null; video_url?: string | null }>
}): string {
  if (project.pipeline_mode === 'image_text') {
    if (isRunning(effectiveStatus(project))) return localeText('当前步骤正在制作，完成后可查看结果并继续下一步。', 'This step is in progress. Check the result and continue when it finishes.')
    const phase = kepuBillingPhase(project)
    if (phase === 'script') return localeText('保存文案后生成分镜，确认每张卡片里的旁白和画面。', 'Save the copy to generate storyboards, then confirm the narration and visual on each card.')
    if (phase === 'assets') return localeText('确认分镜后生成图片与配音；已完成的素材会继续保留。', 'After confirming storyboards, generate images and voiceover; finished assets are kept.')
    return localeText('图片与配音已齐，可以试听后合成带运镜和字幕的成片。', 'Images and voiceover are ready. Preview them, then compose a film with camera motion and subtitles.')
  }
  if (isRunning(effectiveStatus(project))) {
    return translateUi("workflow.generationIsRunningSeeStageProgressOnTheRight")
  }
  const phase = kepuBillingPhase(project)
  if (project.pipeline_mode === 'avatar') {
    if (phase === 'script') return translateUi("workflow.uploadAnUpperBodyPortraitOnTheStylePage")
    if (phase === 'assets') return translateUi("workflow.reviewEachSegmentThenGenerateNarrationEachSegmentHas")
    if (phase === 'videos') return translateUi("workflow.narrationIsReadyNextGenerateTalkingHeadClipsSegment")
    return translateUi("workflow.talkingHeadClipsAreReadyCompositionAddsTitleBars")
  }
  if (phase === 'script') return translateUi("workflow.chooseGenerateStoryboardsOnTheStylePageThisStage")
  if (phase === 'assets') return localeText('确认分镜后，使用画风参考图逐镜生成图片，并参考前镜画面保持角色与风格一致。', 'After confirming the storyboard, generate each shot image from the style reference, using the previous shot to keep characters and style consistent.')
  if (phase === 'videos') return localeText('图片已齐。使用每镜图片生成视频与声音，后镜参考上一镜尾帧延续画面。', 'Images are ready. Generate video and sound from each shot image; later shots continue from the previous last frame.')
  return localeText('镜头视频已齐。检查各镜头画面与声音后，即可合成成片。', 'Shot videos are ready. Check each shot\'s picture and sound, then compose the film.')
}
