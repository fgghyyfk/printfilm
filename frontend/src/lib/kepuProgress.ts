import { translate as translateUi } from '../i18n/runtime'
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

/** 同一运行周期只维护一个串行回读与一条事件流；短查询单独超时，长流不套短限时。 */
export function monitorKepuProgress(options: {
  taskIds: number[]
  connect: (signal: AbortSignal) => Promise<Response>
  load: (signal: AbortSignal) => Promise<Project>
  onProject: (project: Project) => void
  onProgress: (event: ProjectProgress) => void
  onConnectionError?: (message: string | null) => void
  visibility?: Pick<Document, 'hidden' | 'addEventListener' | 'removeEventListener'>
}): () => void {
  const visibility = options.visibility ?? (typeof document === 'undefined' ? undefined : document)
  let controller = new AbortController()
  const taskIds = new Set(options.taskIds)
  let stopped = false
  let generation = 0
  let healthy = false
  let loading = false
  let failures = 0
  let reconnectFailures = 0
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let lastProgress = ''
  const visible = () => !stopped && !visibility?.hidden

  function pause() {
    generation += 1
    controller.abort()
    clearTimeout(pollTimer)
    clearTimeout(reconnectTimer)
    healthy = false
    loading = false
  }
  function stop() {
    stopped = true
    pause()
    visibility?.removeEventListener('visibilitychange', changeVisibility)
  }
  function schedulePoll() {
    clearTimeout(pollTimer)
    if (visible()) pollTimer = setTimeout(() => { void refresh() }, Math.min(30000, (healthy ? 5000 : 1500) * 2 ** failures))
  }

  async function refresh() {
    if (!visible() || loading) return
    const current = generation
    const parent = controller.signal
    const query = new AbortController()
    const abort = () => query.abort()
    parent.addEventListener('abort', abort, { once: true })
    let timedOut = false
    const timeout = setTimeout(() => { timedOut = true; query.abort() }, 15000)
    clearTimeout(pollTimer)
    loading = true
    try {
      const project = await options.load(query.signal)
      if (visible() && generation === current) {
        failures = 0
        options.onConnectionError?.(null)
        options.onProject(project)
        if (!isProjectBusy(project)) stop()
      }
    } catch {
      if (visible() && generation === current && (!query.signal.aborted || timedOut)) {
        failures += 1
        options.onConnectionError?.(translateUi("workflow.connectionLostRetrying"))
      }
    } finally {
      clearTimeout(timeout)
      parent.removeEventListener('abort', abort)
      if (generation === current) { loading = false; schedulePoll() }
    }
  }

  function receive(event: ProjectProgress) {
    if (!visible()) return
    if (event.event !== 'progress') void refresh()
    else if (typeof event.task_id === 'number' && taskIds.has(event.task_id)) {
      const signature = JSON.stringify(event)
      if (signature !== lastProgress) { lastProgress = signature; options.onProgress(event) }
    }
  }

  async function connect() {
    if (!visible()) return
    const current = generation
    const signal = controller.signal
    try {
      const response = await options.connect(signal)
      if (!visible() || generation !== current) { await response.body?.cancel(); return }
      if (!response.body) throw new Error(translateUi("workflow.theProgressConnectionReturnedNoStream"))
      healthy = true
      reconnectFailures = 0
      schedulePoll()
      await readProjectEvents(response.body, (event) => { if (generation === current) receive(event) }, signal)
    } catch { if (generation === current) reconnectFailures += 1 }
    finally {
      if (generation === current) {
        healthy = false
        if (visible()) {
          schedulePoll()
          reconnectTimer = setTimeout(() => { void connect() }, Math.min(30000, 3000 * 2 ** Math.max(0, reconnectFailures - 1)))
        }
      }
    }
  }
  function changeVisibility() {
    pause()
    if (visible()) {
      controller = new AbortController()
      void refresh()
      void connect()
    }
  }
  visibility?.addEventListener('visibilitychange', changeVisibility)
  if (visible()) { schedulePoll(); void connect() }
  return stop
}
