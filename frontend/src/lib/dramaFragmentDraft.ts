/** 分镜草稿的纯函数：判断有无未保存修改、把服务端运行态并入草稿、串行保存。 */
import type { DramaFragment } from '../api/drama'

// 这些 params 键只由服务端写入，不算用户修改；与后端 SERVER_FRAGMENT_PARAMS 对应。
const SERVER_PARAM_KEYS = new Set([
  'video_versions',
  'lastFrameUrl',
  'last_frame_url',
  'generation',
  'generation_attempts',
  'aspect_ratio',
  'resolution',
  'video_width',
  'video_height',
  'video_duration_sec',
  'user_edited',
])

// 取 params 里用户可编辑的部分，按键排序保证比较稳定。
function editableParams(params: unknown): [string, unknown][] {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return []
  return Object.entries(params as Record<string, unknown>)
    .filter(([key]) => !SERVER_PARAM_KEYS.has(key))
    .sort(([a], [b]) => a.localeCompare(b))
}

// 尚未落库的空白分镜是页面占位，不算用户修改。
function isBlankDraft(fragment: DramaFragment): boolean {
  return !(typeof fragment.id === 'number' && fragment.id > 0) && !(fragment.content || '').trim()
}

/** 只覆盖用户可编辑的内容；成片、封面和生成状态的变化不会让页面变成“未保存”。 */
export function fragmentDraftSignature(fragments: DramaFragment[]): string {
  return JSON.stringify(
    fragments
      .filter((fragment) => !isBlankDraft(fragment))
      .map((fragment) => [
        fragment.id,
        fragment.content || '',
        fragment.duration_sec ?? null,
        fragment.asset_ids || [],
        editableParams(fragment.params),
      ]),
  )
}

// 取服务端写入的 params，草稿的用户设置不受影响。
function serverParams(params: unknown): Record<string, unknown> {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return {}
  return Object.fromEntries(
    Object.entries(params as Record<string, unknown>).filter(
      ([key]) => SERVER_PARAM_KEYS.has(key) && key !== 'user_edited',
    ),
  )
}

/**
 * 有未保存修改时刷新：成片和运行态用服务端的，用户内容用草稿的。
 * 草稿里已删除的分镜保持删除；服务端新增的分镜追加到末尾。
 */
export function mergeServerFragmentsIntoDraft(
  draft: DramaFragment[],
  server: DramaFragment[],
  deletedIds: ReadonlySet<number> = new Set(),
): DramaFragment[] {
  const serverById = new Map(server.map((fragment) => [fragment.id, fragment]))
  const draftIds = new Set(draft.map((fragment) => fragment.id))
  const merged = draft.map((fragment) => {
    const latest = serverById.get(fragment.id)
    if (!latest) return fragment
    const draftParams =
      fragment.params && typeof fragment.params === 'object' && !Array.isArray(fragment.params)
        ? Object.fromEntries(
            Object.entries(fragment.params as Record<string, unknown>).filter(([key]) => !SERVER_PARAM_KEYS.has(key) || key === 'user_edited'),
          )
        : {}
    return {
      ...fragment,
      cover: latest.cover,
      video: latest.video,
      params: { ...draftParams, ...serverParams(latest.params) },
    }
  })
  const added = server.filter((fragment) => !draftIds.has(fragment.id) && !deletedIds.has(fragment.id))
  return [...merged, ...added]
}

/**
 * 串行保存：同一时间只发一个请求；进行中收到的新内容合并成一次，等上一个结束后再发最新的。
 * 返回的 Promise 在内容落库（或被更新的内容取代并落库）后完成。
 */
export function createSerialSaver<T>(save: (value: T) => Promise<void>) {
  let running: Promise<void> | null = null
  let hasPending = false
  let pending: T | undefined

  async function drain(): Promise<void> {
    while (hasPending) {
      const value = pending as T
      hasPending = false
      pending = undefined
      await save(value)
    }
  }

  return {
    save(value: T): Promise<void> {
      pending = value
      hasPending = true
      if (!running) {
        running = drain().finally(() => {
          running = null
        })
      }
      return running
    },
    /** 是否还有内容排在当前请求之后。 */
    hasQueued: () => hasPending,
  }
}
