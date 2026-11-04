import type { Project } from '../api'
import { isProjectBusy } from './kepuTaskControls.ts'

/** SSE 是辅助进度；项目状态、素材与是否仍运行始终通过 canonical 回读确认。 */
export type ProjectProgress = {
  event: 'progress' | 'paused' | 'done' | 'failed'
  task_id?: number
  stage?: string
  message?: string
  percent?: number
  shot?: number | null
  total?: number
}

/** 增量读取 SSE 完整帧，支持 UTF-8 跨块、CRLF 和多行 data；取消时释放 reader。 */
export async function readProjectEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ProjectProgress) => void,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let data: string[] = []
  const abort = () => { void reader.cancel().catch(() => undefined) }
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) abort()
  try {
    while (!signal.aborted) {
      const chunk = await reader.read()
      if (chunk.done || signal.aborted) break
      buffer += decoder.decode(chunk.value, { stream: true })
      let newline: number
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '')
        buffer = buffer.slice(newline + 1)
        if (!line) {
          if (event === 'message' && data.length && !signal.aborted) {
            try {
              const payload = JSON.parse(data.join('\n'))
              if (payload && ['progress', 'paused', 'done', 'failed'].includes(payload.event)) onEvent(payload)
            } catch { /* 无效帧不阻断后续事件，canonical 轮询仍可恢复状态。 */ }
          }
          event = 'message'
          data = []
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':')
          const field = colon < 0 ? line : line.slice(0, colon)
          const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
          if (field === 'event') event = value
          if (field === 'data') data.push(value)
        }
      }
    }
  } finally {
    signal.removeEventListener('abort', abort)
    reader.releaseLock()
  }
}

/** 同一运行周期只维护一个串行回读与一条事件流，停止后丢弃所有迟到响应。 */
export function monitorKepuProgress(options: {
  taskIds: number[]
  connect: (signal: AbortSignal) => Promise<Response>
  load: (signal: AbortSignal) => Promise<Project>
  onProject: (project: Project) => void
  onProgress: (event: ProjectProgress) => void
}): () => void {
  const controller = new AbortController()
  const taskIds = new Set(options.taskIds)
  let stopped = false
  let healthy = false
  let loading = false
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let lastProgress = ''

  // 清理同时阻止旧流和旧回读在切项目、到达检查点后回填。
  function stop() {
    stopped = true
    controller.abort()
    clearTimeout(pollTimer)
    clearTimeout(reconnectTimer)
  }

  // 连接健康时低频校验；断流仍按原 1.5 秒节奏回读，避免依赖不完整的 SSE 终态覆盖。
  function schedulePoll() {
    clearTimeout(pollTimer)
    if (!stopped) pollTimer = setTimeout(() => { void refresh() }, healthy ? 5000 : 1500)
  }

  // 终态事件与计时器共用此入口，慢请求期间不启动第二个回读。
  async function refresh() {
    if (stopped || loading) return
    clearTimeout(pollTimer)
    loading = true
    try {
      const project = await options.load(controller.signal)
      if (!stopped) {
        options.onProject(project)
        if (!isProjectBusy(project)) stop()
      }
    } catch { /* 事件流失效或请求短暂失败后继续 canonical 轮询。 */ }
    finally {
      loading = false
      schedulePoll()
    }
  }

  // 有来源的当前任务进度才可显示；旧或无来源终态仅要求重新读取项目。
  function receive(event: ProjectProgress) {
    if (stopped) return
    if (event.event !== 'progress') {
      void refresh()
    } else if (typeof event.task_id === 'number' && taskIds.has(event.task_id)) {
      const signature = JSON.stringify(event)
      if (signature !== lastProgress) {
        lastProgress = signature
        options.onProgress(event)
      }
    }
  }

  // 旧终态可能被服务端重放并立即 EOF，延迟重连防止请求热循环。
  async function connect() {
    try {
      const response = await options.connect(controller.signal)
      if (stopped) {
        await response.body?.cancel()
        return
      }
      if (!response.body) throw new Error('进度连接没有响应流')
      healthy = true
      schedulePoll()
      await readProjectEvents(response.body, receive, controller.signal)
    } catch { /* 无法建立事件流时由下方轮询兜底。 */ }
    finally {
      healthy = false
      if (!stopped) {
        schedulePoll()
        reconnectTimer = setTimeout(() => { void connect() }, 3000)
      }
    }
  }

  schedulePoll()
  void connect()
  return stop
}
