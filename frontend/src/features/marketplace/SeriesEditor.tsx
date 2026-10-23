import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ChevronDown, ChevronRight } from 'lucide-react'
import AssetPicker from './AssetPicker'
import { request, type Handoff } from './api'
import { FormSection, PageHeading, PublishGuide } from './ui'
import Button from '../../components/ui/Button'
import { useCategories } from './useCategories'

type EpisodeDraft = { id?: number; version?: number; episode_no: number; title: string; description?: string; asset_id: number | null; cover_asset_id: number | null; copyright_price_fen: number; unlock_price_fen: number; share_bps?: number | null; source?: 'printfilm' | 'external' | 'original' | 'ai' | 'authorized' }
type SeriesDraft = { id: number; version: number; title: string; description: string; category: string; tags: string[]; cover_asset_id?: number | null; sale_type: 'free' | 'copyright_contact' | 'platform_unlock'; copyright_price_fen: number; free_episode_count?: number; audit_status?: string; listed?: boolean; episodes: EpisodeDraft[] }
type ImportSource = { id: number; title: string; episodes: { id: number; name: string; ready: boolean }[] }

function EpisodePanel({ episode, count, free, open, saleType, seriesId, busy, videoLabel, toggle, update, remove, goRights }: {
  episode: EpisodeDraft; count: number; free: boolean; open: boolean; saleType: SeriesDraft['sale_type']; seriesId?: string; busy: boolean; videoLabel: string
  toggle: () => void; update: (patch: Partial<EpisodeDraft>) => void; remove: () => void; goRights: () => void
}) {
  return <article className={`pf-episode-fold${open ? ' is-open' : ''}`}>
    <button type="button" className="pf-episode-fold-head" aria-expanded={open} onClick={toggle}>
      <span>{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}第 {episode.episode_no} 集 · {episode.title || '未命名'}</span>
      <em>{free ? '免费' : '收费'} · {videoLabel}</em>
    </button>
    {open && <div className="pf-episode-fold-body">
      <label>集标题<span className="pf-req-mark">必填</span><input required value={episode.title} onChange={event => update({ title: event.target.value })} /></label>
      <label>分集简介<textarea value={episode.description || ''} maxLength={5000} onChange={event => update({ description: event.target.value })} placeholder="介绍这一集的内容" /></label>
      <label>内容来源<select value={episode.source || 'external'} onChange={event => update({ source: event.target.value as EpisodeDraft['source'] })}><option value="printfilm">PrintFilm 生成</option><option value="external">其他平台生成</option></select></label>
      <AssetPicker kind="video" value={episode.asset_id} onChange={asset_id => update({ asset_id })} title="分集视频" required />
      <AssetPicker kind="image" required value={episode.cover_asset_id} onChange={cover_asset_id => update({ cover_asset_id })} title="分集封面（可使用系列封面）" />
      {saleType !== 'free' && <label>本集版权参考价（元）<input type="number" min="0" step="0.01" value={(episode.copyright_price_fen / 100) || ''} onChange={event => update({ copyright_price_fen: Math.round(Number(event.target.value || 0) * 100) })} /></label>}
      {episode.asset_id && (seriesId ? <button type="button" onClick={goRights}>填写第 {episode.episode_no} 集权属材料</button> : <p>请先保存草稿，再填写这一集的权属材料。保存后会回到这个页面。</p>)}
      {saleType === 'platform_unlock' && <><label>解锁价（元）<input type="number" min="0.01" step="0.01" value={(episode.unlock_price_fen / 100) || ''} onChange={event => update({ unlock_price_fen: Math.round(Number(event.target.value || 0) * 100) })} /></label><label>本集平台分成（万分比，可留空使用后台默认）<input type="number" min="0" max="10000" step="1" value={episode.share_bps ?? ''} onChange={event => update({ share_bps: event.target.value === '' ? null : Number(event.target.value) })} placeholder="例如 2000" /></label></>}
      {count > 1 && <button type="button" disabled={busy} onClick={remove}>删除本集</button>}
    </div>}
  </article>
}

export default function SeriesEditor() {
  const { id } = useParams(); const navigate = useNavigate()
  const { names: categories, error: categoryError } = useCategories()
  const [series, setSeries] = useState<SeriesDraft | null>(null)
  const [saleType, setSaleType] = useState<'free' | 'copyright_contact' | 'platform_unlock'>('copyright_contact')
  const [freeEpisodeCount, setFreeEpisodeCount] = useState(0)
  const [category, setCategory] = useState('')
  const [coverId, setCoverId] = useState<number | null>(null)
  const [episodes, setEpisodes] = useState<EpisodeDraft[]>([])
  const [removedEpisodeIds, setRemovedEpisodeIds] = useState<number[]>([])
  const [sources, setSources] = useState<ImportSource[]>([]); const [sourceId, setSourceId] = useState(''); const [selected, setSelected] = useState<number[]>([])
  const [handoffs, setHandoffs] = useState<Handoff[]>([])
  const [poll, setPoll] = useState(0)
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set([0]))
  useEffect(() => {
    if (!id) { setEpisodes([{ episode_no: 1, title: '第1集', asset_id: null, cover_asset_id: null, copyright_price_fen: 0, unlock_price_fen: 0 }]); return }
    request<SeriesDraft>(`/series/${id}`).then(value => { setSeries(value); setSaleType(value.sale_type); setFreeEpisodeCount(value.free_episode_count || 0); setCategory(value.category || '其他'); setCoverId(value.cover_asset_id ?? null); setEpisodes(value.episodes.map(item => ({ ...item, asset_id: item.asset_id ?? null, cover_asset_id: item.cover_asset_id ?? null }))) }).catch(e => setError(e.message))
  }, [id])
  useEffect(() => { if (id) request<{ items: ImportSource[] }>('/series/import-sources').then(value => setSources(value.items)).catch(() => {}) }, [id])
  useEffect(() => {
    if (!id) return
    let stop = false
    let timer = 0
    async function tick() {
      try {
        const value = await request<{ items: Handoff[] }>(`/handoffs?series_id=${id}`)
        if (stop) return
        const items = value.items
        setHandoffs(items)
        if (items.some(item => item.status === 'ready')) {
          const fresh = await request<SeriesDraft>(`/series/${id}`)
          if (stop) return
          setEpisodes(current => current.map(episode => {
            const remote = fresh.episodes.find(item => item.id === episode.id)
            if (!remote || (remote.asset_id === episode.asset_id && remote.cover_asset_id === episode.cover_asset_id)) return episode
            return { ...episode, asset_id: remote.asset_id ?? episode.asset_id, cover_asset_id: remote.cover_asset_id ?? episode.cover_asset_id, version: remote.version ?? episode.version }
          }))
        }
        if (items.some(item => item.status === 'pending' || item.status === 'running')) timer = window.setTimeout(() => setPoll(value => value + 1), 3000)
      } catch { /* 进度查询失败时保留当前编辑内容 */ }
    }
    void tick()
    return () => { stop = true; window.clearTimeout(timer) }
  }, [id, poll])
  useEffect(() => { if (!id && !category && categories.length) setCategory(categories.includes('其他') ? '其他' : categories[0]) }, [id, category, categories])
  function update(index: number, patch: Partial<EpisodeDraft>) { setEpisodes(value => value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)) }
  function episodeBody(episode: EpisodeDraft) {
    return { episode_no: episode.episode_no, title: episode.title, description: episode.description || '', asset_id: episode.asset_id, cover_asset_id: episode.cover_asset_id, copyright_price_fen: episode.copyright_price_fen || 0, unlock_price_fen: episode.unlock_price_fen || 0, share_bps: episode.share_bps ?? null, source: episode.source || 'external' }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); if (handoffs.some(item => item.status === 'pending' || item.status === 'running')) { setBusy(false); setError('成片正在同步，同步完成后再保存'); return } const missing = episodes.findIndex(item => !item.title.trim()); if (missing >= 0) { setExpanded(new Set([missing])); setBusy(false); setError(`第 ${missing + 1} 集标题未填写`); return } const form = new FormData(event.currentTarget)
    try {
      const payload = { title: form.get('title'), description: form.get('description'), category: category || '其他', tags: String(form.get('tags') || '').split(/[,，]/).map(value => value.trim()).filter(Boolean), sale_type: saleType, copyright_price_fen: Math.round(Number(form.get('copyright_price') || 0) * 100), free_episode_count: freeEpisodeCount, cover_asset_id: coverId, ...(id ? { version: series?.version } : {}) }
      const saved = await request<SeriesDraft>(id ? `/series/${id}` : '/series', id ? 'PATCH' : 'POST', payload)
      if (!id) for (const episode of episodes) await request(`/series/${saved.id}/episodes`, 'POST', episodeBody(episode))
      else {
        // 先删除再改编号，避免同一系列的集数唯一约束冲突。只提交可编辑字段。
        for (const episodeId of removedEpisodeIds) await request(`/series/${id}/episodes/${episodeId}`, 'DELETE')
        for (const episode of episodes) {
          if (episode.id) await request(`/series/${id}/episodes/${episode.id}`, 'PATCH', { ...episodeBody(episode), version: episode.version })
          else await request(`/series/${id}/episodes`, 'POST', episodeBody(episode))
        }
      }
      if (!id) navigate(`/marketplace/series/${saved.id}/edit`)
      else { const fresh = await request<SeriesDraft>(`/series/${saved.id}`); setSeries(fresh); setSaleType(fresh.sale_type); setFreeEpisodeCount(fresh.free_episode_count || 0); setCategory(fresh.category || '其他'); setCoverId(fresh.cover_asset_id ?? null); setEpisodes(fresh.episodes.map(item => ({ ...item, asset_id: item.asset_id ?? null, cover_asset_id: item.cover_asset_id ?? null }))); setRemovedEpisodeIds([]) }
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function importEpisodes() {
    if (!id || !sourceId || !selected.length) return
    setBusy(true); setError('')
    try { const result = await request<{ items: EpisodeDraft[]; handoffs: Handoff[] }>(`/series/${id}/episodes/import-printfilm`, 'POST', { drama_project_id: Number(sourceId), episode_ids: selected }); setEpisodes(value => [...value, ...result.items]); setHandoffs(value => [...result.handoffs, ...value]); setSelected([]); setExpanded(new Set(result.items.map((_, index) => episodes.length + index))); setPoll(value => value + 1) }
    catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function submit() {
    if (!id || !series) return
    setBusy(true); setError('')
    try { const updated = await request<SeriesDraft>(`/series/${id}/submit`, 'POST', { version: series.version }); setSeries(updated); setEpisodes(updated.episodes); setError('系列已提交，等待管理员在同一次内容审核中核验版权和内容。') }
    catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function toggleListing() {
    if (!id || !series || series.audit_status !== 'approved') return
    setBusy(true); setError('')
    try {
      const updated = await request<SeriesDraft>(`/series/${id}/listing?listed=${!series.listed}`, 'POST', { version: series.version })
      setSeries(updated); setEpisodes(updated.episodes); setError(updated.listed ? '系列已上架到作品广场。' : '系列已下架，历史审核记录仍保留。')
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const current = series
  if (id && !series && !error) return <p role="status">正在加载系列…</p>
  return <><PageHeading title={id ? '编辑短剧' : '发布短剧'} description="短剧和单条视频一样出现在作品广场。在这一页添加分集，版权规则与单视频相同。" back="/marketplace" /><div className="pf-market-editor-layout"><form className="pf-market-editor-main pf-market-form" onSubmit={save}>
    <FormSection step="01" title="系列信息" description="先建立系列，再逐集上传或导入成片。"><label>系列标题<span className="pf-req-mark">必填</span><input name="title" required maxLength={200} placeholder="例如：城市里的光" defaultValue={current?.title} /></label><label>简介<textarea name="description" maxLength={5000} defaultValue={current?.description} placeholder="介绍整部短剧的主题和内容" /></label><div className="pf-market-field-row"><label>分类<span className="pf-req-mark">必填</span><select name="category" required value={category || current?.category || ''} onChange={event => setCategory(event.target.value)} disabled={!categories.length}><option value="" disabled>请选择系列分类</option>{current?.category && !categories.includes(current.category) && <option value={current.category}>{current.category}（历史分类）</option>}{categories.map(name => <option key={name} value={name}>{name}</option>)}</select>{categoryError && <span role="alert">{categoryError}</span>}</label><label>标签<input name="tags" defaultValue={current?.tags?.join('，')} placeholder="都市，短剧" /></label></div><AssetPicker kind="image" value={coverId} onChange={setCoverId} title="系列封面（与分集封面至少上传一处）" /></FormSection>
    <FormSection step="02" title="收益方式" description="平台投放收益需要管理员先配置默认分成比例。"><select value={saleType} onChange={event => setSaleType(event.target.value as typeof saleType)}><option value="free">免费公开</option><option value="copyright_contact">联系客服购买版权</option><option value="platform_unlock">平台投放收益（每集独立解锁）</option></select><label>版权参考价（元）<input name="copyright_price" type="number" min="0" step="0.01" defaultValue={current?.copyright_price_fen ? current.copyright_price_fen / 100 : ''} /></label><label>免费集数<span className="pf-req-mark">前几集免费</span><input type="number" min="0" max="10000" step="1" value={freeEpisodeCount} onChange={event => setFreeEpisodeCount(Math.max(0, Number(event.target.value) || 0))} /></label><p className="pf-market-notice">免费集播放原片，不生成水印。选择“免费公开”时全部分集免费；其他收费类型可将前几集设为免费，其余分集按收费方式观看。只改集数或收费类型、不重新上传视频时，不会重新处理成片。</p></FormSection>
    <FormSection step="03" title="分集内容" description="每集一个视频和封面。点标题展开这一集，收起后内容仍会保存。"><div className="pf-episode-tools"><button type="button" onClick={() => setExpanded(new Set(episodes.map((_, index) => index)))}>展开全部</button><button type="button" onClick={() => setExpanded(new Set())}>收起全部</button><span>共 {episodes.length} 集</span></div><div className="pf-market-episodes">{episodes.map((episode, index) => { const handoff = handoffs.find(item => item.episode_id === episode.id); const videoLabel = handoff && handoff.status !== 'ready' ? handoff.label : (episode.asset_id ? '视频已上传' : '缺视频'); return <EpisodePanel key={episode.id ?? `new-${index}`} episode={episode} count={episodes.length} free={saleType === 'free' || index < freeEpisodeCount} open={expanded.has(index)} saleType={saleType} seriesId={id} busy={busy} videoLabel={videoLabel} toggle={() => setExpanded(value => { const next = new Set(value); if (next.has(index)) next.delete(index); else next.add(index); return next })} update={patch => update(index, patch)} remove={() => { if (episode.id) setRemovedEpisodeIds(value => [...value, episode.id as number]); setEpisodes(value => value.filter((_, itemIndex) => itemIndex !== index).map((item, itemIndex) => ({ ...item, episode_no: itemIndex + 1 }))); setExpanded(new Set([Math.max(0, index - 1)])) }} goRights={() => navigate(`/marketplace/assets/${episode.asset_id}/rights?source=${episode.source || 'external'}&return=${encodeURIComponent(`/marketplace/series/${id}/edit`)}`)} /> })}</div><button type="button" onClick={() => { setEpisodes(value => [...value, { episode_no: value.length + 1, title: `第${value.length + 1}集`, asset_id: null, cover_asset_id: null, copyright_price_fen: 0, unlock_price_fen: 0, source: 'external' }]); setExpanded(new Set([episodes.length])) }}>+ 添加下一集</button></FormSection>
    {id && <FormSection step="04" title="从我的 PrintFilm 漫剧导入" description="只导入勾选的分集。服务器复制到市场后，这里会显示正在同步、已就绪或复制失败可重试。"><select value={sourceId} onChange={event => { setSourceId(event.target.value); setSelected([]) }}><option value="">选择漫剧项目</option>{sources.map(source => <option key={source.id} value={source.id}>{source.title}</option>)}</select>{sources.find(source => String(source.id) === sourceId)?.episodes.map(episode => <label key={episode.id} className="block"><input type="checkbox" disabled={!episode.ready} checked={selected.includes(episode.id)} onChange={event => setSelected(value => event.target.checked ? [...value, episode.id] : value.filter(item => item !== episode.id))} /> {episode.name} {episode.ready ? '' : '（成片未就绪）'}</label>)}<button type="button" disabled={busy || !sourceId || !selected.length} onClick={() => void importEpisodes()}>导入选中分集</button>{handoffs.filter(item => item.status !== 'ready').map(item => <p key={item.id} role={item.status === 'failed' ? 'alert' : 'status'}>{item.label}{item.error ? `：${item.error}` : ''}{item.status === 'failed' ? <button type="button" onClick={() => void request<Handoff>(`/handoffs/${item.id}/retry`, 'POST').then(next => { setHandoffs(value => value.map(row => row.id === next.id ? next : row)); setPoll(value => value + 1) })}>重试复制</button> : null}{item.status === 'ready' && !item.authorization_ready ? <button type="button" onClick={() => navigate('/marketplace/authorization')}>去签署授权函</button> : null}</p>)}</FormSection>}
    {error && <p role={error.includes('已') ? 'status' : 'alert'}>{error}</p>}<div className="pf-market-form-actions"><Button variant="lime" type="submit" disabled={busy || !categories.length || !category}>{busy ? '保存中…' : '保存系列草稿'}</Button>{id && (series?.audit_status === 'draft' || series?.audit_status === 'preview_failed') && <button type="button" disabled={busy} onClick={() => void submit()}>{series?.audit_status === 'preview_failed' ? '重试生成水印并提交' : '提交系列审核'}</button>}{id && series?.audit_status === 'processing' && <p role="status">正在生成水印播放文件，完成后自动进入待审核。</p>}{id && series?.audit_status === 'approved' && <button type="button" disabled={busy} onClick={() => void toggleListing()}>{series.listed ? '下架系列' : '上架到作品广场'}</button>}</div>
  </form><PublishGuide /></div></>
}
