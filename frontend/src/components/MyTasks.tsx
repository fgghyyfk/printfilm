import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { tasksApi, type TaskRunOut } from '../api/tasks'
import Modal from './ui/Modal'
import './MyTasks.css'

const OPEN_TASKS = 'pf-open-my-tasks'
const terminal = (task: TaskRunOut) => ['succeeded', 'failed', 'cancelled'].includes(task.status)

export function MyTasksButton() {
  return <button type="button" className="pf-nav-help-btn" onClick={() => window.dispatchEvent(new Event(OPEN_TASKS))}>我的任务</button>
}

function taskName(task: TaskRunOut) {
  const names: Record<string, string> = {
    project_pipeline: '视频创作', project_compose_only: '成片合成', project_regen_audio: '整片配音',
    shot_regen_audio: '镜头配音', shot_regen_image: '镜头出图', shot_regen_video: '镜头视频',
    fragment_video: '分镜视频', fragment_plan: 'AI 分镜', episode_compose: '分集合成',
    asset_image: '资产出图', asset_video: '资产视频', voice_synthesis: '音色合成', tool_video: '工具视频',
  }
  const domains: Record<string, string> = { kepu: '视频创作', drama: '漫剧生成', studio: '工具生成', tools: '工具任务' }
  return names[task.task_type] || domains[task.domain] || '生成任务'
}

function taskStatus(task: TaskRunOut) {
  if (task.cancel_requested && !terminal(task)) return '等待停止'
  const labels: Record<string, string> = { pending: '排队中（位置未知）', leased: '准备中', running: '生成中', awaiting_poll: '上游生成中',
    awaiting_review: '费用待核对', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
  return labels[task.status] || '处理中'
}

function taskHref(task: TaskRunOut) {
  if (task.project_id) return `/studio/${task.project_id}`
  if (task.drama_project_id) return task.episode_id
    ? `/drama/projects/${task.drama_project_id}/episodes/${task.episode_id}` : `/drama/projects/${task.drama_project_id}`
  return '/settings?tab=tools'
}

/** 常驻但仅持当前账号的观察状态；历史第一页缺席绝不当成任务完成。 */
export default function MyTasks() {
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
      const message = `${taskName(task)} #${task.id}${task.status === 'succeeded' ? '已完成' : task.status === 'failed' ? '失败，请查看原因' : '已取消'}`
      setNotice(message)
      const original = titleRef.current?.original || document.title
      const applied = `【任务更新】${original}`
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
        if (valid(token)) { failures += 1; setError('连接中断，重试中') }
      } finally {
        clearTimeout(timeout)
        running = false
        if (!stopped && !document.hidden) timer = setTimeout(() => { void poll() }, Math.min(30000, 5000 * 2 ** failures))
      }
    }
    const wake = () => {
      clearTimeout(timer)
      controller?.abort()
      // 恢复后重新建立观察基线，不把隐藏期间历史终态批量当作新完成。
      trackedRef.current.clear()
      resetAccount(localStorage.getItem('token'))
      if (!document.hidden) void poll()
    }
    document.addEventListener('visibilitychange', wake)
    window.addEventListener('storage', wake)
    void poll()
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', wake); window.removeEventListener('storage', wake) }
  }, [location.key, open, page])

  return <>
    {notice ? <div className="pf-task-toast" role="status">{notice}<button type="button" onClick={() => { setOpen(true); clearNotice() }}>查看</button><button type="button" aria-label="关闭提醒" onClick={clearNotice}>×</button></div> : null}
    <Modal open={open} onClose={() => setOpen(false)} title={`我的任务${activeCount ? ` · ${activeCount} 项处理中` : ''}`} variant="drawer">
      <p>排队位置由实际调度决定，当前位置未知。</p>
      {error ? <p role="status">{error}</p> : null}
      {!items.length && !error ? <p>暂无任务</p> : null}
      <ul className="pf-my-tasks-list">{items.map(task => <li key={task.id}>
        <Link to={taskHref(task)} onClick={() => setOpen(false)}>{taskName(task)} #{task.id}</Link>
        <span>{taskStatus(task)}</span>
        {!terminal(task) ? <progress max={100} value={Math.max(0, Math.min(100, task.progress_percent || 0))} /> : null}
        {task.error_message ? <p>{task.error_message}</p> : null}
      </li>)}</ul>
      <div className="pf-my-tasks-pages"><button type="button" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>{page} / {Math.max(1, Math.ceil(total / 20))}</span><button type="button" disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>下一页</button></div>
    </Modal>
  </>
}
