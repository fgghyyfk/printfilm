import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, LoaderCircle, RefreshCw } from 'lucide-react'
import { creationApi } from '../../api/creation'
import { isRequestAborted } from '../../api/http'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import './shortVideoCreate.css'

/** 旧会话先等待生产者退出，再安全移交普通分步工作台。 */
export default function CreationSessionEditorBridge() {
  const { sessionId } = useParams()
  const [params] = useSearchParams()
  const projectId = Number(params.get('project')) || undefined
  const returnPath = `/studio/sessions/${sessionId}${projectId ? `?project=${projectId}` : ''}`
  const nav = useNavigate()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [waiting, setWaiting] = useState(false)
  useEffect(() => {
    if (!sessionId) return
    if (!localStorage.getItem('token')) {
      nav(`/auth?next=${encodeURIComponent(returnPath)}`, { replace: true })
      return
    }
    let disposed = false
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    setError('')
    async function transfer() {
      try {
        const project = await creationApi.openEditor(sessionId!, controller.signal, projectId)
        if (disposed) return
        nav(project.shots.length || project.final_video_url ? `/studio/${project.id}` : `/studio/new?project=${project.id}`, { replace: true })
      } catch (cause) {
        if (disposed || isRequestAborted(cause)) return
        const status = cause && typeof cause === 'object' && 'status' in cause ? Number(cause.status) : 0
        if (status === 409 && cause instanceof Error && cause.message.includes('正在停止旧创作任务')) {
          setWaiting(true)
          timer = setTimeout(() => { void transfer() }, 2000)
        } else if (status === 401) {
          nav(`/auth?next=${encodeURIComponent(returnPath)}`, { replace: true })
        } else setError(cause instanceof Error ? cause.message : '暂时无法打开项目，请重试')
      }
    }
    void transfer()
    return () => { disposed = true; controller.abort(); if (timer) clearTimeout(timer) }
  }, [sessionId, projectId, returnPath, nav, attempt])
  return <AppShell active="studio"><div className="sv-create-page">
    <Link to="/history" className="sv-back"><ArrowLeft size={16} /> 我的短片</Link>
    <section className="sv-panel" style={{ marginTop: 24 }}>
      <h2>打开分步工作台</h2>
      <p>{waiting ? '正在停止旧制作任务，已有文案与素材会保留。任务退出后自动进入编辑。' : '正在整理项目文案与素材，准备继续编辑。'}</p>
      {error ? <><BillingErrorNotice message={error} /><Button variant="lime" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} /> 重试</Button></> : <div className="sv-loading"><LoaderCircle size={20} className="sv-spin" /> {waiting ? '等待原任务退出…' : '正在打开…'}</div>}
    </section>
  </div></AppShell>
}
