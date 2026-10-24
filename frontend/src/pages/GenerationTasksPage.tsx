import { useEffect, useState } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, ArrowUpRight, CheckCircle2, Clock3, Download, Eye, ListFilter, Loader2, Plus, RefreshCw, Search, X } from 'lucide-react'
import { listToolRuns, resolveToolMediaUrl, type ToolRunRecord } from '../api/tools'
import GenerationLayout from '../components/tools/GenerationLayout'
import GenerationDateRange from '../components/tools/GenerationDateRange'
import GenerationSelect from '../components/tools/GenerationSelect'
import { mediaIcons, mediaKinds, type MediaKind, useGenerationText } from '../lib/generationMedia'
import Modal from '../components/ui/Modal'
import Pagination from '../components/ui/Pagination'
import { fetchMediaBlob, triggerBlobDownload } from '../lib/clientDownload'

export default function GenerationTasksPage() {
  if (!localStorage.getItem('token')) return <Navigate to="/auth?next=/tasks" replace />
  return <TaskHistory />
}

function TaskHistory() {
  const text = useGenerationText()
  const [params, setParams] = useSearchParams()
  const kind = mediaKinds.includes(params.get('kind') as MediaKind) ? params.get('kind') as MediaKind : 'video'
  const status = ['processing', 'succeeded', 'failed', 'cancelled'].includes(params.get('status') || '') ? params.get('status')! : ''
  const taskId = params.get('task_id') || ''
  const [query, setQuery] = useState(taskId)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(8)
  const [items, setItems] = useState<ToolRunRecord[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [detail, setDetail] = useState<ToolRunRecord | null>(null)
  const [downloading, setDownloading] = useState<number | null>(null)
  const Icon = mediaIcons[kind]
  const labels: Record<string, string> = {
    all: text('全部', 'All'), processing: text('处理中', 'Processing'), succeeded: text('已完成', 'Completed'),
    failed: text('失败', 'Failed'), cancelled: text('已取消', 'Cancelled'), queued: text('排队中', 'Queued'), running: text('处理中', 'Processing'),
  }
  function filter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    setParams(next); setPage(1)
  }
  useEffect(() => { setQuery(taskId) }, [taskId])
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    setLoading(true)
    async function refresh() {
      if (from && to && from > to) { setError(text('开始日期不能晚于结束日期', 'Start date must precede end date')); setLoading(false); return }
      try {
        const data = await listToolRuns(page, pageSize, { kind, status, task_id: taskId,
          created_from: from ? new Date(`${from}T00:00:00`).toISOString() : '',
          created_to: to ? new Date(`${to}T23:59:59.999`).toISOString() : '' })
        if (disposed) return
        setItems(data.items); setTotal(data.total); setCounts(data.counts || {}); setError('')
        if (page > Math.max(1, Math.ceil(data.total / pageSize))) setPage(Math.max(1, Math.ceil(data.total / pageSize)))
        timer = setTimeout(refresh, 5000)
      } catch (err) {
        if (!disposed) { setError(err instanceof Error ? err.message : text('加载失败', 'Loading failed')); timer = setTimeout(refresh, 10000) }
      } finally { if (!disposed) setLoading(false) }
    }
    void refresh()
    return () => { disposed = true; clearTimeout(timer) }
  }, [kind, status, taskId, from, to, page, pageSize, revision, text])

  async function download(item: ToolRunRecord, url = item.urls[0]) {
    setDownloading(item.id)
    try {
      triggerBlobDownload(await fetchMediaBlob(resolveToolMediaUrl(url)), `printfilm_${item.kind}_${item.id}.${item.kind === 'video' ? 'mp4' : item.kind === 'audio' ? 'mp3' : 'png'}`)
    } catch (err) { setError(err instanceof Error ? err.message : text('下载失败', 'Download failed')) }
    finally { setDownloading(null) }
  }
  function when(value?: string | null) {
    return value ? new Date(value).toLocaleString(text('zh-CN', 'en-US'), { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
  }
  function elapsed(item: ToolRunRecord) {
    if (!item.started_at || !item.finished_at) return '—'
    const seconds = Math.max(0, Math.round((Date.parse(item.finished_at) - Date.parse(item.started_at)) / 1000))
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  }
  function errorSummary(error: string) {
    if (/sensitive|policyviolation|content.?filter|审核/i.test(error)) return text('内容未通过审核，请调整描述后重试', 'Content review failed. Try adjusting your prompt.')
    if (/余额不足|insufficient.*balance/i.test(error)) return text('余额不足，请充值后重试', 'Insufficient balance. Please top up.')
    if (/timeout|timed out|超时/i.test(error)) return text('生成超时，请稍后重试', 'Generation timed out. Please try again.')
    return error.length > 90 || /^[{[]/.test(error) ? text('生成未完成，点击查看详情', 'Generation failed. View details for more information.') : error
  }
  const hasFilters = Boolean(status || taskId || from || to)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const StatusIcon = { succeeded: CheckCircle2, failed: AlertCircle, running: Clock3, queued: Clock3, cancelled: X }
  return <GenerationLayout tasks>
    <header className="gen-tasks-heading">
      <div><div className="gen-section-eyebrow">{text('创作管理', 'CREATION LIBRARY')}</div><h1>{text('我的任务', 'My tasks')}<span className="gen-heading-dot" /></h1><p>{text('每一次灵感，都有迹可循。', 'Every idea, all in one place.')}</p></div>
      <div className="gen-task-actions"><button className="gen-secondary" type="button" onClick={() => setRevision(value => value + 1)} disabled={loading}><RefreshCw size={16} className={loading ? 'gen-spin' : ''} />{text('刷新', 'Refresh')}</button><Link className="gen-primary" to={`/tools/${kind}`}><Plus size={18} />{text('去创作', 'Create something')}</Link></div>
    </header>
    <div className="gen-library">
      <div className="gen-task-tabs"><div role="tablist" aria-label={text('任务类型', 'Media type')}>
        {mediaKinds.map((value, index) => {
          const TabIcon = mediaIcons[value]
          return <button type="button" key={value} id={`gen-tab-${value}`} role="tab" tabIndex={value === kind ? 0 : -1}
            aria-selected={value === kind} className={value === kind ? 'is-active' : ''} onClick={() => filter('kind', value)}
            onKeyDown={event => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
              event.preventDefault()
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3
              filter('kind', mediaKinds[next]); document.getElementById(`gen-tab-${mediaKinds[next]}`)?.focus()
            }}><TabIcon size={18} />{text(value === 'video' ? '视频任务' : value === 'image' ? '图片任务' : '音频任务', `${value[0].toUpperCase()}${value.slice(1)} tasks`)}</button>
        })}
      </div><span className="gen-auto-sync"><i />{text('自动更新', 'Auto-updating')}</span></div>
      <div className="gen-task-filters">
        <div className="gen-status-tabs" aria-label={text('任务状态', 'Task status')}>
          {['all', 'processing', 'succeeded', 'failed', 'cancelled'].map(value => <button type="button" key={value} aria-pressed={(status || 'all') === value} className={(status || 'all') === value ? 'is-active' : ''} onClick={() => filter('status', value === 'all' ? '' : value)}>{labels[value]}<span>{counts[value] || 0}</span></button>)}
        </div>
        <div className="gen-filter-inputs">
          <form className={`gen-search ${taskId ? 'has-value' : ''}`} role="search" onSubmit={event => { event.preventDefault(); filter('task_id', query.trim()) }}>
            <Search size={18} aria-hidden /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={text('搜索任务 ID 或上游 ID', 'Search task or upstream ID')} aria-label={text('搜索任务 ID', 'Search task ID')} />
            {query && <button className="gen-search-clear" type="button" aria-label={text('清除搜索', 'Clear search')} onClick={() => { setQuery(''); filter('task_id', '') }}><X size={14} /></button>}
            <button type="submit" className="gen-search-submit" aria-label={text('搜索', 'Search')}><ArrowUpRight size={17} /></button>
          </form>
          <GenerationDateRange from={from} to={to} onChange={(start, end) => { setFrom(start); setTo(end); setPage(1) }} />
          {hasFilters && <button type="button" className="gen-reset-filters" onClick={() => { setFrom(''); setTo(''); setQuery(''); setParams({ kind }); setPage(1) }}>{text('重置筛选', 'Reset filters')}</button>}
        </div>
      </div>
      {error && <p className="gen-inline-error" role="alert">{error}</p>}
      <div className="gen-list-heading" aria-hidden><span><ListFilter size={14} />{text('生成内容', 'Creation')}</span><span>{text('任务状态', 'Status')}</span><span>{text('费用', 'Cost')}</span><span>{text('创建时间', 'Created')}</span><span>{text('操作', 'Actions')}</span></div>
      <section className="gen-task-list" aria-label={text('生成任务记录', 'Generation history')} aria-busy={loading}>
      {loading && !items.length ? <div className="gen-empty"><Loader2 className="gen-spin" size={28} />{text('加载任务中…', 'Loading tasks…')}</div> : !items.length ? <div className="gen-empty"><Icon size={36} /><strong>{text('暂无符合条件的任务', 'No matching tasks')}</strong><p>{text('调整筛选条件，或开始一次新的创作。', 'Adjust your filters or create something new.')}</p><Link to={`/tools/${kind}`}>{text('开始创作', 'Start creating')} →</Link></div> : items.map(item => {
        const RowStatusIcon = StatusIcon[item.status as keyof typeof StatusIcon] || Clock3
        return <article className={`gen-task-row ${item.status === 'failed' ? 'has-error' : ''}`} key={item.id}>
        <button className="gen-task-cover" type="button" onClick={() => setDetail(item)} aria-label={text('查看生成结果', 'View result')}>
          {item.kind === 'image' && (item.preview_url || item.urls[0]) ? <img src={resolveToolMediaUrl(item.preview_url || item.urls[0])} alt="" /> : item.kind === 'video' && item.preview_url ? <img src={resolveToolMediaUrl(item.preview_url)} alt="" /> : item.kind === 'video' && item.urls[0] ? <video src={resolveToolMediaUrl(item.urls[0])} preload="metadata" muted /> : <Icon size={28} />}
          <span className="gen-cover-overlay"><Eye size={18} /></span>
        </button>
        <div className="gen-task-description"><button type="button" className="gen-task-title" onClick={() => setDetail(item)} title={item.prompt}>{item.prompt || text('未命名创作', 'Untitled creation')}</button><div className="gen-task-metadata"><span className="gen-task-tag">{item.params?.ratio || (kind === 'audio' ? text('文字配音', 'Text to speech') : 'AI')}</span><span className="gen-task-number">#{item.platform_task_id || item.id}</span>{item.provider_task_id && <code title={item.provider_task_id}>{text('上游', 'Upstream')} {item.provider_task_id}</code>}</div>{item.error && <button type="button" className="gen-task-error" onClick={() => setDetail(item)} title={item.error}><AlertCircle size={12} /><span>{errorSummary(item.error)}</span><ArrowUpRight size={12} /></button>}</div>
        <div className="gen-task-state"><span className={`gen-state is-${item.status}`}><RowStatusIcon size={13} />{labels[item.status] || item.status}</span><small>{item.kind === 'video' ? 'MP4' : item.kind === 'audio' ? 'MP3' : text('图片', 'Image')}</small></div>
        <div className="gen-task-cost"><strong>¥{((item.billing_charged_fen || 0) / 100).toFixed(2)}</strong><small>{item.finished_at ? text('已结算', 'Charged') : text('待结算', 'Pending charge')}</small></div>
        <div className="gen-task-time"><span>{when(item.created_at)}</span><small>{text('耗时', 'Duration')} {elapsed(item)}</small></div>
        <div className="gen-row-actions">{item.status === 'succeeded' && item.urls[0] && <button type="button" className="gen-secondary" disabled={downloading === item.id} onClick={() => void download(item)}>{downloading === item.id ? <Loader2 size={15} className="gen-spin" /> : <Download size={15} />}{text('下载', 'Download')}</button>}<button type="button" className="gen-icon-button" onClick={() => setDetail(item)} aria-label={text('查看任务详情', 'Task details')}><Eye size={18} /></button></div>
      </article>})}
    </section>
    <footer className="gen-task-pagination"><span>{text('共', 'Total')} <strong>{total}</strong> {text('条记录', 'creations')}<span className="gen-pagination-divider" />{text(`第 ${page} / ${pageCount} 页`, `Page ${page} of ${pageCount}`)}</span>
      <div><Pagination page={page} pageCount={pageCount} onChange={setPage} showSummary={false} /><GenerationSelect compact label={text('每页条数', 'Items per page')} value={String(pageSize)} options={[8, 16, 24, 50].map(value => ({ value: String(value), label: text(`${value} 条 / 页`, `${value} / page`) }))} onChange={value => { setPageSize(Number(value)); setPage(1) }} /></div>
    </footer>
    </div>
    <Modal open={Boolean(detail)} title={text('生成详情', 'Generation details')} onClose={() => setDetail(null)} className="gen-detail-modal">
      {detail && <div className="gen-detail"><p className="gen-detail-prompt">{detail.prompt}</p><dl>
        <div><dt>{text('任务 ID', 'Task ID')}</dt><dd>{detail.task_id || detail.id}</dd></div>
        <div><dt>{text('调度队列', 'Queue')}</dt><dd>{{ text: text('文字', 'Text'), image: text('图片', 'Image'), video: text('视频', 'Video') }[detail.queue_type || ''] || '—'}</dd></div>
        <div><dt>{text('上游任务 ID', 'Upstream task ID')}</dt><dd>{detail.provider_task_id || '—'}</dd></div>
        <div><dt>{text('上游服务 / 状态', 'Provider / status')}</dt><dd>{detail.provider_name || '—'} / {detail.provider_status || '—'}</dd></div>
        <div><dt>{text('耗时', 'Duration')}</dt><dd>{elapsed(detail)}</dd></div>
      </dl>{detail.error && <p className="gen-inline-error">{detail.error}</p>}{detail.urls.map(url => <div key={url} className="gen-detail-media">{detail.kind === 'video' ? <video controls src={resolveToolMediaUrl(url)} /> : detail.kind === 'audio' ? <audio controls src={resolveToolMediaUrl(url)} /> : <img src={resolveToolMediaUrl(url)} alt={detail.prompt} />}<button type="button" className="gen-secondary" onClick={() => void download(detail, url)} disabled={downloading === detail.id}><Download size={16} />{text('下载结果', 'Download result')}</button></div>)}</div>}
    </Modal>
  </GenerationLayout>
}
