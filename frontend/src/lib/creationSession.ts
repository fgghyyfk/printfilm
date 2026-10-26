import type { CreationRun, CreationSession } from '../api/creation.ts'

export const CREATION_PHASES = [
  {
    key: 'planning',
    label: '创作文案',
    description: '正在把你的想法写成适合讲述的文案',
  },
  {
    key: 'script',
    label: '编排脚本',
    description: '正在安排镜头、旁白和画面节奏',
  },
  {
    key: 'assets',
    label: '绘制与配音',
    description: '正在绘制动画画面，并生成旁白配音',
  },
  {
    key: 'compose',
    label: '合成短片',
    description: '正在加入运镜、字幕和配音，合成最终视频',
  },
] as const

const BUSY_SESSION = new Set([
  'thinking',
  'generating',
  'planning',
  'producing',
])
const BUSY_RUN = new Set([
  'pending',
  'queued',
  'running',
  'thinking',
  'generating',
  'planning',
  'producing',
])

export function activeCreationRun(
  session: Pick<CreationSession, 'active_run_id' | 'runs'> | null,
): CreationRun | undefined {
  if (!session) return undefined
  return (
    session.runs.find(
      (run) => String(run.id) === String(session.active_run_id),
    ) || [...session.runs].sort((a, b) => b.version - a.version)[0]
  )
}

export function isCreationBusy(
  session: Pick<CreationSession, 'status' | 'runs' | 'active_run_id'> | null,
): boolean {
  if (!session) return false
  if (
    [
      'completed',
      'done',
      'failed',
      'cancelled',
      'drafting',
      'collecting',
    ].includes(session.status)
  )
    return false
  return (
    BUSY_SESSION.has(session.status) ||
    BUSY_RUN.has(activeCreationRun(session)?.status || '')
  )
}

export function creationPhaseIndex(phase: string): number {
  if (phase === 'done') return CREATION_PHASES.length
  if (phase === 'copy') return 0
  return Math.max(
    0,
    CREATION_PHASES.findIndex((item) => item.key === phase),
  )
}

export function creationStatusLabel(status: string): string {
  return (
    (
      {
        drafting: '构思中',
        collecting: '构思中',
        thinking: '正在思考',
        planning: '正在思考',
        generating: '制作中',
        producing: '制作中',
        completed: '已完成',
        done: '已完成',
        failed: '需要重试',
        cancelled: '已停止',
      } as Record<string, string>
    )[status] || '创作中'
  )
}

/** 同一条消息失败后重发时保留此 ID，由服务端避免重复创建与计费。 */
export function creationMessageId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ||
    `msg-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
  )
}

export type CreationSubmission = {
  content: string
  templateId?: string
  sessionId?: string
  id: string
}

/** 默认3D交给后端；只有明确选择的风格覆盖自然语言决策。 */
export function buildCreationSubmission(options: {
  content: string
  selectedTemplateId: string
  sessionId?: string
  explicitTemplateId?: string
  previous?: CreationSubmission | null
}): CreationSubmission | null {
  const content = options.content.trim()
  if (!content) return null
  const templateId =
    options.explicitTemplateId ??
    (options.sessionId || options.selectedTemplateId === 'anim_3d'
      ? undefined
      : options.selectedTemplateId)
  const previous = options.previous
  if (
    previous &&
    previous.content === content &&
    previous.templateId === templateId &&
    previous.sessionId === options.sessionId
  )
    return previous
  return {
    content,
    templateId,
    sessionId: options.sessionId,
    id: creationMessageId(),
  }
}
