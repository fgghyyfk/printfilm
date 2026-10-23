import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Film, Play, RefreshCw } from 'lucide-react'
import { date, labels, money, request, type Listing, type Row } from './api'
import { Media, Pager } from './shared'
import Button from '../../components/ui/Button'
import { EmptyState, PageHeading } from './ui'
import AssetPreview from './AssetPreview'
import { dialog } from '../../lib/dialog'

function mineHref(item: Row) {
  return item.kind === 'series' ? `/marketplace/series/${item.id}/edit` : `/marketplace/works/${item.id}/manage`
}

function mineStatus(item: Row) {
  if (item.blocked) return '已限制'
  if (item.listed) return '已上架'
  return labels[item.audit_status || ''] || '草稿'
}

function freeMine(item: Row) {
  return item.sale_type === 'free' || ((item.price_fen ?? 0) === 0 && item.sale_type !== 'platform_unlock')
}

function MineWorks({ listing, loading, page, changePage, remove }: { listing: Listing | null; loading: boolean; page: number; changePage: (value: number) => void; remove: (item: Row) => void }) {
  return <section className="pf-market-discover pf-market-mine">
    <header className="pf-discover-heading"><div><span className="pf-market-kicker">单视频与短剧</span><h1>我的作品</h1><p>草稿、审核和已上架的作品都在这里。</p></div><Button variant="dark" to="/marketplace/works/new" icon>发布作品<ArrowUpRight size={17} /></Button></header>
    {loading || !listing ? <div key="loading" className="pf-market-grid" role="status">{[0, 1, 2].map(item => <div key={item} className="pf-market-skeleton"><div /><span /><span /></div>)}</div> : listing.items.length ? <div key="ready" className="pf-market-grid">{listing.items.map(item => {
      const href = mineHref(item)
      const free = freeMine(item)
      return <article className="pf-market-item" key={`${item.kind}-${item.id}`}>
        <div className="pf-market-item-cover">{item.cover_asset_id ? <Media kind={item.kind === 'series' ? 'series' : 'work'} id={item.id} purpose="cover" /> : <span className="pf-market-series-mark"><Film size={36} /></span>}<Link className="pf-market-cover-play" to={href} aria-label={`管理${item.title}`}><Play size={16} /></Link></div>
        <div className="pf-market-item-body">
          <h3><Link to={href}>{item.title}</Link></h3>
          <p className="pf-market-author"><span>{item.kind === 'series' ? '剧' : '作'}</span>{mineStatus(item)}{item.kind === 'series' ? ` · ${item.episode_count || 0} 集` : ''}</p>
          <div className="pf-market-item-price"><div><strong>{free ? '免费' : money(item.sale_type === 'platform_unlock' ? item.unlock_price_fen : item.price_fen)}</strong>{!free && <span>{item.sale_type === 'platform_unlock' ? '解锁价' : '参考价'}</span>}</div><button type="button" onClick={() => remove(item)}>删除</button><Link to={href} aria-label={`管理${item.title}`}><ArrowUpRight size={20} /></Link></div>
        </div>
      </article>
    })}</div> : <EmptyState title="还没有作品" description="单条视频和短剧都会出现在这里。" action="发布我的作品" to="/marketplace/works/new" />}
    <Pager listing={listing} page={page} change={changePage} />
  </section>
}

// 所有列表由后端按当前账号隔离；不与原个人中心数据混合。
export default function Workspace() {
  /* kind 工作台分类；listing/page 当前列表；refresh 重新读取；mediaId 当前文件预览。 */
  const [kind, setKind] = useState('works')
  const [listing, setListing] = useState<Listing | null>(null)
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [mediaId, setMediaId] = useState<number | null>(null)
  useEffect(() => { let active = true; setLoading(true); setListing(null); setError(''); request<Listing>(`/mine/${kind}?page=${page}`).then(value => { if (active) setListing(value) }).catch(e => { if (active) setError(e.message) }).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [kind, page, refresh])
  async function action(path: string) { try { await request(path, 'POST'); setRefresh(v => v + 1) } catch (e) { setError((e as Error).message) } }
  async function removeOwned(item: Row) {
    const ok = await dialog.confirm({ title: '删除作品', message: `删除后「${item.title || '未命名作品'}」会从我的作品和作品广场撤下。文件和审核记录会保留，但不能再展示。`, confirmText: '删除', tone: 'danger' })
    if (!ok || !item.id) return
    try { await request(item.kind === 'series' ? `/series/${item.id}` : `/works/${item.id}`, 'DELETE'); setRefresh(value => value + 1) } catch (e) { setError((e as Error).message) }
  }
  const tabs = { works: '我的作品', series: '我的短剧', recruits: '我的招募', rights: '版权审核', assets: '我的文件', notifications: '站内通知' }
  return <><PageHeading title="我的发布与业务进度" description="从作品草稿到合作机会，在这里跟进作品发布与需求审核。招募接单请联系客服。" /><p><Link to="/marketplace/authorization">签署 / 查看 PrintFilm 授权函</Link></p>
    <div className="pf-market-workspace-toolbar"><nav className="pf-market-tabs" aria-label="个人业务分类">{Object.entries(tabs).map(([key, label]) => <button key={key} type="button" aria-pressed={key === kind} onClick={() => { setKind(key); setPage(1); setMediaId(null); setListing(null); setLoading(true) }}>{label}</button>)}</nav><button type="button" onClick={() => setRefresh(v => v + 1)}><RefreshCw size={14} />刷新</button></div>
    {error && <p role="alert">{error}</p>}
    {kind === 'works' ? <MineWorks listing={listing} loading={loading} page={page} changePage={setPage} remove={item => void removeOwned(item)} /> : <>
      {kind === 'series' && <section className="pf-market-discover pf-market-mine"><header className="pf-discover-heading"><div><span className="pf-market-kicker">剧集资产</span><h1>我的短剧</h1><p>查看系列草稿、分集审核和上架状态。</p></div><Button variant="dark" to="/marketplace/works/new?type=series" icon>发布短剧<ArrowUpRight size={17} /></Button></header>{loading || !listing ? <div key="loading" className="pf-market-grid" role="status">{[0, 1, 2].map(item => <div key={item} className="pf-market-skeleton"><div /><span /><span /></div>)}</div> : listing.items.length ? <div key="ready" className="pf-market-grid">{listing.items.map(item => { const href = `/marketplace/series/${item.id}/edit`; const free = item.sale_type === 'free'; const price = item.copyright_price_fen ?? item.price_fen; return <article className="pf-market-item" key={`series-${item.id}`}><div className="pf-market-item-cover">{item.cover_asset_id ? <Media kind="series" id={item.id} purpose="cover" /> : <span className="pf-market-series-mark"><Film size={36} /></span>}<Link className="pf-market-cover-play" to={href} aria-label={`管理${item.title}`}><Play size={16} /></Link></div><div className="pf-market-item-body"><h3><Link to={href}>{item.title}</Link></h3><p className="pf-market-author"><span>剧</span>{mineStatus(item)} · {item.episode_count || 0} 集</p><div className="pf-market-item-price"><div><strong>{free ? '免费' : item.sale_type === 'platform_unlock' ? '按集解锁' : money(price)}</strong>{!free && <span>{item.sale_type === 'platform_unlock' ? '分集定价' : '参考价'}</span>}</div><Link to={href} aria-label={`管理${item.title}`}><ArrowUpRight size={20} /></Link></div></div></article> })}</div> : <EmptyState title="还没有短剧" description="创建系列后，可逐集上传或导入漫剧分集。" action="发布短剧" to="/marketplace/works/new?type=series" />}<Pager listing={listing} page={page} change={setPage} /></section>}
      {kind !== 'series' && <>{loading ? <div className="pf-market-empty" role="status">正在读取你的业务进度…</div> : listing?.items.map(item => <article className="pf-market-workspace-item" key={item.id}>
        <div className="pf-market-record-head"><h2>{item.title || item.filename || item.recruit_title || item.message || `记录 #${item.id}`}</h2><span className="pf-market-badge">{item.blocked ? '已限制' : item.withdrawn ? '已撤回' : labels[item.audit_status ?? item.status ?? item.state ?? item.selection ?? ''] || (kind === 'notifications' ? item.read ? '已读' : '未读' : '业务记录')}</span></div>
        <p>{date(item.created_at)} · 编号 #{item.id}</p>
        {(item.reason || item.note || item.error) && <p className="pf-market-record-note">{item.reason || item.note || item.error}</p>}
        {kind === 'recruits' && <Link to={`/marketplace/recruits/${item.id}/manage`}>查看与管理</Link>}
        {kind === 'assets' && <><p>类型：{item.kind} · 指纹：{item.fingerprint || '处理后生成'}</p><button type="button" onClick={() => setMediaId(mediaId === item.id ? null : item.id)}>{mediaId === item.id ? '收起预览' : '查看文件预览'}</button>{mediaId === item.id && <AssetPreview id={item.id} />}{item.status === 'failed' && <button type="button" onClick={() => action(`/assets/${item.id}/retry`)}>重试处理</button>}{item.kind === 'video' && item.status === 'ready' && <Link to={`/marketplace/assets/${item.id}/rights?return=${encodeURIComponent('/marketplace/mine')}`}>查看版权审核</Link>}</>}
        {kind === 'rights' && <><p>审核状态：{labels[item.full_status ?? '']}</p><Link to={`/marketplace/assets/${item.asset_id}/rights?return=${encodeURIComponent('/marketplace/mine')}`}>查看与补充材料</Link></>}
        {kind === 'notifications' && <>{item.link && <Link to={item.link}>前往工作台</Link>}<button type="button" disabled={item.read} onClick={() => action(`/notifications/${item.id}/read`)}>{item.read ? '已读' : '标记已读'}</button></>}
      </article>)}
      {!loading && !error && listing?.items.length === 0 && <EmptyState title="这里还没有记录" description={kind === 'notifications' ? '发布及审核状态发生变化时，通知会出现在这里。' : '你的需求与合作进展，都会在这里留下记录。'} action={kind === 'recruits' ? '发布招募需求' : undefined} to={kind === 'recruits' ? '/marketplace/recruits/new' : '/marketplace'} />}
      <Pager listing={listing} page={page} change={setPage} /></>}
    </>}
  </>
}
