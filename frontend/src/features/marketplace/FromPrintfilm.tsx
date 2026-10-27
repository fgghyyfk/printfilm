import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { request, type Handoff } from './api'
import { PageHeading } from './ui'
import Button from '../../components/ui/Button'
import DramaPublishModal from './DramaPublishModal'

type ProjectSource = { id: number; title: string; updated_at: string; has_cover: boolean; status: string }
type DramaSource = { id: number; title: string; episodes: { id: number; name: string; ready: boolean }[] }
type SeriesOption = { id: number; title: string }

// 从 PrintFilm 已生成成片发起转存。页面只提交编号，不提交视频地址。
export default function FromPrintfilm() {
  const navigate = useNavigate()
  const [search] = useSearchParams()
  const focusProject = Number(search.get('project') || 0)
  const focusDrama = Number(search.get('drama') || 0)
  const focusEpisode = Number(search.get('episode') || 0)
  const [projects, setProjects] = useState<ProjectSource[]>([])
  const [dramas, setDramas] = useState<DramaSource[]>([])
  const [series, setSeries] = useState<SeriesOption[]>([])
  const [dramaId, setDramaId] = useState(focusDrama ? String(focusDrama) : '')
  const [dramaPublishOpen, setDramaPublishOpen] = useState(Boolean(focusDrama))
  const [seriesId, setSeriesId] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let stop = false
    Promise.all([
      request<{ projects: ProjectSource[]; dramas: DramaSource[] }>('/printfilm/sources'),
      request<{ items: SeriesOption[] }>('/mine/series?page_size=100'),
    ]).then(([sources, mine]) => {
      if (stop) return
      setProjects(sources.projects)
      setDramas(sources.dramas)
      setSeries(mine.items)
      setDramaId(current => current || (sources.dramas[0] ? String(sources.dramas[0].id) : ''))
    }).catch(err => { if (!stop) setError((err as Error).message) }).finally(() => { if (!stop) setLoading(false) })
    return () => { stop = true }
  }, [])

  const drama = dramas.find(item => String(item.id) === dramaId)
  const focused = useMemo(() => projects.find(item => item.id === focusProject), [projects, focusProject])

  async function publishWork(project: ProjectSource) {
    setBusy(true); setError('')
    try {
      const handoff = await request<Handoff>('/works/from-printfilm', 'POST', {
        project_id: project.id,
        idempotency_key: `kepu-${project.id}-${project.updated_at}`,
      })
      navigate(`/marketplace/works/${handoff.work_id}/edit?handoff=${handoff.id}`)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  async function ensureSeries(title: string) {
    if (seriesId && seriesId !== 'new') return Number(seriesId)
    const created = await request<SeriesOption>('/series', 'POST', {
      title: title || '未命名短剧', category: '其他', sale_type: 'copyright_contact', copyright_price_fen: 0, description: '',
    })
    return created.id
  }

  async function addProject(project: ProjectSource) {
    setBusy(true); setError('')
    try {
      const target = await ensureSeries(project.title)
      const result = await request<{ handoffs: Handoff[] }>(`/series/${target}/episodes/import-printfilm`, 'POST', {
        project_id: project.id,
        idempotency_key: `kepu-${project.id}-${project.updated_at}`,
      })
      const handoff = result.handoffs[0]
      navigate(`/marketplace/series/${target}/edit${handoff ? `?handoff=${handoff.id}` : ''}`)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  if (loading) return <p role="status">正在读取已生成的成片…</p>
  return <>
    <PageHeading title="发布到数字资产市场" description="选择 PrintFilm 已生成的成片，同步到市场后补充资料并提交审核。已签署有效授权函的账号会自动关联，未签署的请在提交前完成一次签署。" back="/marketplace/works/new" />
    {error && <p role="alert">{error}</p>}
    <section className="pf-market-form">
      <h2>科普成片</h2>
      <p className="pf-market-notice">发布为单视频会生成一条草稿。添加到短剧系列会把这部成片作为一集，不改已经发布过的旧版本。</p>
      <label>添加到短剧<select value={seriesId} onChange={event => setSeriesId(event.target.value)}><option value="">选择已有短剧，或新建</option><option value="new">新建短剧</option>{series.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      {projects.length === 0 ? <p>还没有可发布的科普成片。</p> : projects.map(project => <article key={project.id} className="pf-market-handoff">
        <strong>{project.title}{project.id === focusProject ? ' · 当前成片' : ''}</strong>
        <div>
          <Button variant="lime" type="button" disabled={busy} onClick={() => void publishWork(project)}>发布为单视频</Button>
          <button type="button" disabled={busy || !seriesId} onClick={() => void addProject(project)}>添加到短剧系列</button>
        </div>
      </article>)}
      {focused ? null : focusProject ? <p role="status">这部成片还没有生成完成，暂时不能发布。</p> : null}
    </section>
    <section className="pf-market-form">
      <h2>漫剧分集</h2>
      <p className="pf-market-notice">只同步勾选的完整分集。完成后，在短剧编辑页补充封面、价格和作品介绍，再提交审核；有效账号授权函会自动关联。</p>
      {dramas.length === 0 ? <p>还没有可导入的漫剧分集。</p> : <>
        <label>漫剧项目<select value={dramaId} onChange={event => setDramaId(event.target.value)}>{dramas.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <button type="button" disabled={busy || !drama} onClick={() => setDramaPublishOpen(true)}>选择完整分集并发布</button>
      </>}
    </section>
    {dramaPublishOpen && drama && <DramaPublishModal projectId={drama.id} initialEpisodeId={focusEpisode || undefined} onClose={() => setDramaPublishOpen(false)} />}
  </>
}
