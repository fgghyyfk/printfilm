import { useI18n } from '../i18n'
import { translate as translateUi } from '../i18n/runtime'
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { tasksApi, type TaskRunOut } from '../api/tasks'
import Modal from './ui/Modal'
import './MyTasks.css'

const OPEN_TASKS = 'pf-open-my-tasks'
const terminal = (task: TaskRunOut) => ['succeeded', 'failed', 'cancelled'].includes(task.status)

export function MyTasksButton() {
  const { t: translateUi } = useI18n()
  return <button type="button" className="pf-nav-help-btn" onClick={() => window.dispatchEvent(new Event(OPEN_TASKS))}>{translateUi("workflow.myTasks")}</button>
}

function taskName(task: TaskRunOut) {
  const names: Record<string, string> = {
    project_pipeline: translateUi("workflow.videoCreation"), project_compose_only: translateUi("workflow.finalVideoComposition"), project_regen_audio: translateUi("workflow.fullVoiceover"),
    shot_regen_audio: translateUi("workflow.shotVoiceover"), shot_regen_image: translateUi("workflow.shotImage"), shot_regen_video: translateUi("workflow.videoClips"),
    fragment_video: translateUi("workflow.storyboardVideo"), fragment_plan: translateUi("workflow.aiStoryboards"), episode_compose: translateUi("workflow.episodeComposition"),
    asset_image: translateUi("workflow.assetImage"), asset_video: translateUi("workflow.assetVideo"), voice_synthesis: translateUi("workflow.voiceSynthesis"), tool_video: translateUi("workflow.toolVideo"),
  }
  const domains: Record<string, string> = { kepu: translateUi("workflow.videoCreation"), drama: translateUi("workflow.dramaGeneration"), studio: translateUi("workflow.toolGeneration"), tools: translateUi("workflow.toolTask") }
  return names[task.task_type] || domains[task.domain] || translateUi("workflow.generationTask")
}

function taskStatus(task: TaskRunOut) {
  if (task.cancel_requested && !terminal(task)) return translateUi("workflow.waitingToStop")
  const labels: Record<string, string> = { pending: translateUi("workflow.queuedPositionUnknown"), leased: translateUi("workflow.preparing"), running: translateUi("ecomSet.result.running"), awaiting_poll: translateUi("workflow.providerIsGenerating"),
    awaiting_review: translateUi("workflow.costAwaitingReview"), succeeded: translateUi("ecomSet.status.done"), failed: translateUi("ecomSet.status.failed"), cancelled: translateUi("workflow.cancelled") }
  return labels[task.status] || translateUi("workflow.processing")
}

function taskHref(task: TaskRunOut) {
  if (task.project_id) return `/studio/${task.project_id}`
  if (task.drama_project_id) return task.episode_id
    ? `/drama/projects/${task.drama_project_id}/episodes/${task.episode_id}` : `/drama/projects/${task.drama_project_id}`
  return '/settings?tab=tools'
}

/** 常驻但仅持当前账号的观察状态；历史第一页缺席绝不当成任务完成。 */
export default function MyTasks() {
  const { t: translateUi } = useI18n()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<TaskRunOut[]>([])
  const [total, setTotal] = useState(0)
  const [activeCount, setActiveCount] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const tokenRef = useRef<string | null>(null)
  const trackedRef = useRef(new Map<number, TaskRunOut>())
  const announcedRef = useRef(new Set<number>())
  const titleRef = useRef<{ original: string; applied: string } | null>(null)
  const clearNotice = () => {
    setNotice('')
    const title = titleRef.current
    if (title && document.title === title.applied) document.title = title.original
    titleRef.current = null
  }

  useEffect(() => {
    const show = () => { setPage(1); setOpen(true); clearNotice() }
    window.addEventListener(OPEN_TASKS, show)
    window.addEventListener('focus', clearNotice)
    return () => { window.removeEventListener(OPEN_TASKS, show); window.removeEventListener('focus', clearNotice); clearNotice() }
  }, [])

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    let running = false
    let failures = 0
    const valid = (token: string) => !stopped && !document.hidden && localStorage.getItem('token') === token
    const resetAccount = (token: string | null) => {
      if (tokenRef.current === token) return
      tokenRef.current = token
      trackedRef.current.clear()
      announcedRef.current.clear()
      setItems([]); setTotal(0); setActiveCount(0); setError(''); clearNotice()
      if (!token) setOpen(false)
    }
    const notify = (task: TaskRunOut, token: string) => {
      if (!valid(token) || announcedRef.current.has(task.id)) return
      announcedRef.current.add(task.id)
      const message = `${taskName(task)} #${task.id}: ${task.status === 'succeeded' ? translateUi("ecomSet.status.done") : task.status === 'failed' ? translateUi("workflow.failedViewTheReason") : translateUi("workflow.cancelled")}`
      setNotice(message)
      const original = titleRef.current?.original || document.title
      const applied = translateUi("workflow.taskUpdate", { v1: original })
      titleRef.current = { original, applied }
      document.title = applied
    }
    async function poll() {
      if (stopped || running || document.hidden) return
      const token = localStorage.getItem('token')
      resetAccount(token)
      if (!token) return
      running = true
      controller = new AbortController()
      const timeout = setTimeout(() => controller?.abort(), 15000)
      try {
        const active: TaskRunOut[] = []
        let currentPage = 1
        let activeTotal = 0
        do {
          const result = await tasksApi.list({ active_only: true, page: currentPage, page_size: 100, signal: controller.signal })
          if (!valid(token)) return
          active.push(...result.items)
          activeTotal = result.total
          currentPage += 1
        } while (active.length < activeTotal && (currentPage - 1) * 100 < activeTotal)
        const ids = new Set(active.map((task) => task.id))
        // 完整活动列表中消失后，逐个回读该任务的真实终态，不按列表长度推断。
        for (const [id] of trackedRef.current) {
          if (ids.has(id)) continue
          try {
            const task = await tasksApi.get(id, { signal: controller.signal })
            if (!valid(token)) return
            if (terminal(task)) { notify(task, token); trackedRef.current.delete(id) }
          } catch (err) {
            if ((err as { status?: number }).status === 404) trackedRef.current.delete(id)
            else throw err
          }
        }
        if (!valid(token)) return
        for (const task of active) trackedRef.current.set(task.id, task)
        setActiveCount(activeTotal)
        if (open) {
          const result = await tasksApi.list({ page, page_size: 20, signal: controller.signal })
          if (!valid(token)) return
          setItems(result.items); setTotal(result.total)
        }
        failures = 0
        setError('')
      } catch {
        if (valid(token)) { failures += 1; setError(translateUi("workflow.connectionLostRetrying")) }
      } finally {
        clearTimeout(timeout)
        running = false
        if (!stopped && !document.hidden) timer = setTimeout(() => { void poll() }, Math.min(30000, 5000 * 2 ** failures))
      }
    }
    const wake = () => {
      clearTimeout(timer)
      controller?.abort()
      // 同账号恢复仍核对已观察的在途任务；从未观察的历史终态不提醒。
      resetAccount(localStorage.getItem('token'))
      if (!document.hidden) void poll()
    }
    document.addEventListener('visibilitychange', wake)
    window.addEventListener('storage', wake)
    void poll()
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', wake); window.removeEventListener('storage', wake) }
  }, [location.key, open, page])

  return <>
    {notice ? <div className="pf-task-toast" role="status">{notice}<button type="button" onClick={() => { setOpen(true); clearNotice() }}>{translateUi("workflow.view")}</button><button type="button" aria-label={translateUi("workflow.dismissNotification")} onClick={clearNotice}>×</button></div> : null}
    <Modal open={open} onClose={() => setOpen(false)} title={translateUi("workflow.myTasks_954b7f", { v1: String(activeCount ? translateUi("workflow.inProgress", { v1: String(activeCount) }) : '') })} variant="drawer">
      <p>{translateUi("workflow.queueOrderDependsOnSchedulingYourPositionIsCurrently")}</p>
      {error ? <p role="status">{error}</p> : null}
      {!items.length && !error ? <p>{translateUi("workflow.noTasksYet")}</p> : null}
      <ul className="pf-my-tasks-list">{items.map(task => <li key={task.id}>
        <Link to={taskHref(task)} onClick={() => setOpen(false)}>{taskName(task)} #{task.id}</Link>
        <span>{taskStatus(task)}</span>
        {!terminal(task) ? <progress max={100} value={Math.max(0, Math.min(100, task.progress_percent || 0))} /> : null}
        {task.error_message ? <p>{task.error_message}</p> : null}
      </li>)}</ul>
      <div className="pf-my-tasks-pages"><button type="button" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>{translateUi("workflow.previous")}</button><span>{page} / {Math.max(1, Math.ceil(total / 20))}</span><button type="button" disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>{translateUi("workflow.next")}</button></div>
    </Modal>
  </>
}
