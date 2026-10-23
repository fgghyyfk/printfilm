import CategoryForm from './CategoryForm'
import { useEffect, useState, type FormEvent } from 'react'
import { request, assetUrl, useMarketplaceEnabled, type Item, type Listing } from './api'
import { PaginationBar } from '@/components/PaginationBar'
import { RefreshCw, Film, ShieldCheck, Users, Flag, CircleAlert, MessageCircle, Inbox } from 'lucide-react'
import './marketplace-admin.css'

// 独立下游运营工作台；不复用或改变原作品审核页。
const businessTabs = { works: '作品审核', series: '短剧审核', recruits: '招募审核', reports: '举报处理', support: '客服配置', categories: '作品分类', revenue: '平台分成' }
const maintenanceTabs = { previews: '作品试看任务', submissions: '历史投稿记录', jobs: '历史媒体任务', orders: '平台订单', entitlements: '永久播放权益', ledger: '收益账本', payouts: '人工打款', events: '操作记录' }
const tabs = { ...businessTabs, ...maintenanceTabs }
const statusNames: Record<string, string> = { preview_failed: '试看生成失败', original: '免费原片', missing: '未生成', running: '处理中', done: '已完成', draft: '草稿', pending: '待审核', approved: '已通过', rejected: '已驳回', ready: '已就绪', processing: '处理中', failed: '失败', open: '征集中', closed: '已关闭', expired: '已到期', pending_materials: '待材料', reviewing: '材料核验中', supplement: '待补正', applying: '办理中', certificated: '已取得证书', cancelled: '已取消', to_review: '待看', candidate: '候选', not_selected: '不采用', active: '有效权益', inactive: '已停用', printfilm: 'PrintFilm 生成', external: '其他平台生成', existing: '已有证书', self_service: '自行办理', assisted: '客服协办', free: '免费公开', copyright_contact: '联系客服购买版权', platform_unlock: '平台投放收益' }
const names: Record<string, string> = { preview_status: '试看状态', preview_error: '试看错误', playback_status: '播放版本', playback_error: '播放错误', work_id: '作品编号', id: '编号', owner_id: '用户编号', user_id: '购买用户编号', asset_id: '媒体编号', publication_id: '发布版本编号', publication_version: '发布版本', episode_id: '分集编号', order_id: '订单编号', content_type: '内容类型', content_id: '内容编号', publication_owner_id: '创作者编号', active: '权益有效', version: '内容版本', title: '标题', description: '说明', price_fen: '参考价（分）', sale_type: '收费类型', copyright_price_fen: '版权参考价（分）', unlock_price_fen: '解锁价（分）', free_episode_count: '前置免费集数', episode_count: '分集数量', platform_share_bps: '平台分成（万分比）', share_bps: '单内容分成（万分比）', gross_fen: '订单金额（分）', platform_fen: '平台收益（分）', creator_fen: '创作者收益（分）', amount_fen: '金额（分）', budget_fen: '预算（分）', quote_fen: '报价（分）', category: '分类', source: '来源', source_type: '来源类型', source_snapshot: '导入来源快照', license_scope: '授权意向', audit_status: '内容审核', basic_status: '基础权属', full_status: '完整权属', reason: '审核原因', listed: '作者上架', blocked: '封禁', author: '作者', holder: '权利人', relationship: '发布人与权利人的关系', acquisition: '权利取得方式', scope: '授权范围', third_party: '第三方授权', institution: '登记机构', certificate_number: '证书号', ai_platform_license: 'AI平台许可', ai_generation_record: '生成记录', human_contribution: '人工贡献', official_reference: '申请编号', route: '办理方式', status: '状态', note: '备注', statement: '举报说明', contact: '联系渠道', resolution: '处理结果', target_type: '对象类型', target_id: '对象编号', requirements: '交付要求', wanted_count: '征集数量', deadline_at: '截止时间', valid_until: '授权截止', fingerprint: '文件指纹', filename: '文件名', selection: '筛选标记', withdrawn: '撤回', action: '操作', actor_id: '操作人', attempts: '尝试次数', error: '错误说明', media_status: '媒体状态', state: '业务状态', tags: '标签', authorization_id: '授权函编号', authorization_version: '授权条款版本', authorization_signer: '签署人' }

// 按可读字段渲染审核材料，原片和证书另经鉴权打开。
function Summary({ item }: { item: Item }) {
  const details = typeof item.details === 'object' && item.details !== null ? item.details : {}
  return <dl className="market-ops-summary">
    {Object.entries(item).filter(([key, value]) => names[key] && !['basic_status', 'full_status', 'id', 'title', 'audit_status', 'status'].includes(key) && value !== null && value !== '').map(([key, value]) => <div key={key}><dt>{names[key]}</dt><dd>{typeof value === 'boolean' ? value ? '是' : '否' : ['price_fen', 'copyright_price_fen', 'unlock_price_fen', 'budget_fen', 'quote_fen', 'gross_fen', 'platform_fen', 'creator_fen', 'amount_fen'].includes(key) ? `¥${(Number(value) / 100).toFixed(2)}` : Array.isArray(value) ? value.join('、') || '—' : typeof value === 'object' ? JSON.stringify(value) : statusNames[String(value)] || String(value)}</dd></div>)}
    {Object.entries(details).filter(([, value]) => value !== '' && value !== null && value !== undefined).map(([key, value]) => <div key={key}><dt>{key === 'dispute_ack' ? '版权声明已确认' : names[key] || key}</dt><dd>{typeof value === 'boolean' ? value ? '是' : '否' : statusNames[String(value)] || String(value)}</dd></div>)}
  </dl>
}

// 直接从服务端签名，不将原始 OSS key 传到页面。
function FileLink({ id, label, purpose = 'original', video = false }: { id: number; label: string; purpose?: string; video?: boolean }) {
  const [url, setUrl] = useState(''); const [error, setError] = useState('')
  return <div><button type="button" className="underline" onClick={async () => { try { setUrl(await assetUrl(id, purpose)); setError('') } catch (e) { setError((e as Error).message) } }}>{label} #{id}</button>{url && (video ? <video src={url} controls className="my-3 max-h-[500px] w-full" /> : <a className="ml-3 underline" href={url} target="_blank" rel="noreferrer">打开文件（链接5分钟有效）</a>)}{error && <p role="alert">{error}</p>}</div>
}

function WorkPreviewLink({ id }: { id: number }) {
  const [url, setUrl] = useState(''); const [error, setError] = useState('')
  return <div><button type="button" className="underline" onClick={async () => { try { setUrl((await request<{ url: string }>(`/api/marketplace/media/work/${id}/preview`)).url); setError('') } catch (e) { setError((e as Error).message) } }}>查看公众水印试看版</button>{url && <video src={url} controls className="my-3 max-h-[500px] w-full" />}{error && <p role="alert">{error}</p>}</div>
}
function AuthorizationLink({ details }: { details: unknown }) {
  const [url, setUrl] = useState(''); const [error, setError] = useState('')
  const id = details && typeof details === 'object' && 'authorization_id' in details ? Number(details.authorization_id) : 0
  if (!id) return null
  return <div><button type="button" className="underline" onClick={async () => { try { setUrl((await request<{ url: string }>(`/api/marketplace/authorizations/${id}/letter`)).url); setError('') } catch (e) { setError((e as Error).message) } }}>查看已签署 PrintFilm 授权函 #{id}</button>{url && <a href={url} target="_blank" rel="noreferrer"><img src={url} alt="已签署授权函" className="my-3 max-h-[600px] max-w-full object-contain" /></a>}{error && <p role="alert">{error}</p>}</div>
}

function PayoutStatusForm({ item, onSaved }: { item: Item; onSaved: () => void }) {
  const [status, setStatus] = useState(String(item.status || 'pending'))
  const [note, setNote] = useState(String(item.note || ''))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await request(`/api/admin/marketplace/payouts/${item.id}`, 'PATCH', { owner_id: Number(item.owner_id), amount_fen: Number(item.amount_fen), status, note })
      onSaved()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="market-ops-inline-form" onSubmit={save}>
    <label>打款状态<select value={status} onChange={event => setStatus(event.target.value)} disabled={status === 'paid'}><option value="pending">待结算</option><option value="paid">已打款</option><option value="failed">打款失败</option></select></label>
    <label>备注<input value={note} maxLength={3000} onChange={event => setNote(event.target.value)} /></label>
    <button type="submit" disabled={busy || status === String(item.status || '')}>{busy ? '保存中…' : '更新打款记录'}</button>
    {error && <span role="alert">{error}</span>}
  </form>
}


// 独立工作台含分页、错误态、敏感材料检查和明确原因输入。
export default function MarketplaceAdmin() {
  const enabled = useMarketplaceEnabled()
  const [kind, setKind] = useState<keyof typeof tabs>('works')
  const [listing, setListing] = useState<Listing | null>(null)
  const [overview, setOverview] = useState<Record<string, number>>({})
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [maintenanceOpen, setMaintenanceOpen] = useState(false)
  const [entitlementUserId, setEntitlementUserId] = useState('')
  const [entitlementOwnerId, setEntitlementOwnerId] = useState('')
  function navigateTab(next: keyof typeof tabs, nextStatus = '') {
    if (next === 'jobs' || next === 'events' || next === 'previews' || next === 'submissions' || next === 'orders' || next === 'entitlements' || next === 'ledger' || next === 'payouts') setMaintenanceOpen(true)
    setKind(next); setPage(1); setStatus(nextStatus); setListing(null); setError(''); setMessage('')
  }
  useEffect(() => {
    if (!enabled || kind === 'support' || kind === 'categories' || kind === 'revenue') return
    let active = true; setLoading(true); setError('')
    const params = new URLSearchParams({ page: String(page), status })
    if (kind === 'entitlements') { if (entitlementUserId) params.set('user_id', entitlementUserId); if (entitlementOwnerId) params.set('owner_id', entitlementOwnerId) }
    request<Listing>(`/api/admin/marketplace/records/${kind}?${params}`).then(result => { if (active) setListing(result) }).catch(e => { if (active) setError(e.message) }).finally(() => { if (active) setLoading(false) })
    request<Record<string, number>>('/api/admin/marketplace/overview').then(value => { if (active) setOverview(value) }).catch(() => {})
    return () => { active = false }
  }, [enabled, kind, page, status, refresh, entitlementUserId, entitlementOwnerId])
  // 统一提交审核意图，后端始终重新核对版本与状态。
  async function action(path: string, body: unknown) { setError(''); setMessage(''); try { await request(`/api/admin/marketplace${path}`, 'POST', body); setRefresh(v => v + 1); setMessage('操作成功') } catch (e) { setError((e as Error).message) } }
  // 审核原因和逐项确认来自该记录的表单，不自动勾选。
  function review(e: FormEvent<HTMLFormElement>, item: Item) {
    e.preventDefault(); const form = new FormData(e.currentTarget); const submitter = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement
    void action(`/${kind}/${item.id}/review`, { version: item.version, decision: submitter.value, reason: form.get('reason'), checks: form.getAll('checks') })
  }
  if (!enabled) return <p>下游业务未启用，或无法读取服务状态。</p>
  return <div className="market-ops">
    <header className="market-ops-header"><div><div className="market-ops-eyebrow"><ShieldCheck size={15} /> 运营工作台</div><h1>数字资产平台运营</h1><p>集中处理作品、招募与版权审核，跟进举报及客服咨询。</p></div><button type="button" className="market-ops-refresh" onClick={() => setRefresh(v => v + 1)} disabled={loading}><RefreshCw size={16} className={loading ? 'animate-spin' : ''} />刷新数据</button></header>
    <div className="market-ops-stats">{([
      { key: 'works_pending', label: '待审作品', icon: Film, tab: 'works', tone: 'green' },
      { key: 'recruits_pending', label: '待审招募', icon: Users, tab: 'recruits', tone: 'blue' },
      { key: 'reports_pending', label: '待处理举报', icon: Flag, tab: 'reports', tone: 'amber' },
      { key: 'jobs_failed', label: '失败媒体任务', icon: CircleAlert, tab: 'jobs', tone: 'red' },
    ] as const).map(card => <button type="button" className={`market-ops-stat tone-${card.tone}`} key={card.key} onClick={() => navigateTab(card.tab, card.tab === 'jobs' ? 'failed' : 'pending')}><span className="market-ops-stat-icon"><card.icon size={20} /></span><span><span className="market-ops-stat-label">{card.label}</span><strong>{overview[card.key] ?? '—'}</strong></span><span className="market-ops-stat-arrow">↗</span></button>)}</div>
    <div className="market-ops-context"><span><MessageCircle size={15} />累计咨询 <strong>{overview.consultations ?? '—'}</strong> 次</span><span>文件容量 <strong>{overview.storage_bytes === undefined ? '—' : `${(overview.storage_bytes / 1024 / 1024).toFixed(1)} MB`}</strong></span><span className="market-ops-context-note">合同、收款与交付由客服线下协调</span></div>
    <nav className="market-ops-tabs" aria-label="数字资产运营业务">{Object.entries(businessTabs).map(([key, label]) => <button type="button" key={key} aria-current={kind === key ? 'page' : undefined} onClick={() => navigateTab(key as keyof typeof tabs)}>{label}</button>)}<button type="button" aria-expanded={maintenanceOpen} className="market-ops-maintenance-toggle" onClick={() => { if (maintenanceOpen && (kind === 'jobs' || kind === 'events' || kind === 'previews' || kind === 'submissions' || kind === 'orders' || kind === 'entitlements' || kind === 'ledger' || kind === 'payouts')) navigateTab('works'); setMaintenanceOpen(!maintenanceOpen) }}>运行维护 {maintenanceOpen ? '−' : '+'}</button></nav>
    {maintenanceOpen && <div className="market-ops-maintenance"><div><strong>运行维护</strong><p>查看作品试看任务、订单权益、历史投稿及平台操作记录。招募接单由客服线下对接。</p></div><nav aria-label="运行维护">{Object.entries(maintenanceTabs).map(([key, label]) => <button type="button" key={key} aria-current={kind === key ? 'page' : undefined} onClick={() => navigateTab(key as keyof typeof tabs)}>{label}</button>)}</nav></div>}
    {kind === 'jobs' && <p className="market-ops-feedback">此处仅保留旧版媒体任务，招募不再创建投稿或审片任务；付费作品的限时水印预览请查看“作品试看任务”。</p>}
    {error && <p role="alert" className="market-ops-feedback is-error">{error}</p>}{message && <p role="status" className="market-ops-feedback">{message}</p>}
    {kind === 'support' ? <SupportForm /> : kind === 'categories' ? <CategoryForm /> : kind === 'revenue' ? <RevenueConfigForm /> : <><div className="market-ops-filter"><div><h2>{tabs[kind]}</h2><span>{listing ? `共 ${listing.meta.total} 条记录` : '正在加载记录'}</span></div><select aria-label="状态筛选" className="rounded border bg-background p-2" value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}><option value="">全部状态</option>{kind === 'entitlements' ? <><option value="active">有效权益</option><option value="inactive">已停用</option></> : Object.entries(statusNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{kind === 'entitlements' && <div className="market-ops-entitlement-filter"><input aria-label="购买用户编号" type="number" min="1" placeholder="购买用户编号" value={entitlementUserId} onChange={e => { setEntitlementUserId(e.target.value); setPage(1) }} /><input aria-label="创作者编号" type="number" min="1" placeholder="创作者编号" value={entitlementOwnerId} onChange={e => { setEntitlementOwnerId(e.target.value); setPage(1) }} /></div>}<button type="button" onClick={() => setRefresh(v => v + 1)}>刷新</button></div>{kind === 'payouts' && <PayoutCreateForm onSaved={() => setRefresh(v => v + 1)} />}{loading ? <p role="status">正在加载…</p> : listing?.items.map(item => <article className="market-ops-record" key={`${kind}-${item.id}-${refresh}`}><header className="market-ops-record-head"><div><span className="market-ops-record-id">#{item.id} · {tabs[kind]}</span><h3>{String(item.title || item.filename || item.recruit_title || (kind === 'entitlements' ? `用户 #${item.user_id} 的永久权益` : '业务记录'))}</h3></div><span className={`market-ops-badge status-${String(item.audit_status || item.status || item.selection || (kind === 'entitlements' ? (item.active ? 'active' : 'inactive') : 'draft'))}`}>{statusNames[String(item.audit_status || item.status || item.selection || (kind === 'entitlements' ? (item.active ? 'active' : 'inactive') : 'draft'))] || (kind === 'entitlements' ? (item.active ? '有效' : '已停用') : String(item.status || '记录'))}</span></header><details className="market-ops-details" open={kind === 'works' || kind === 'recruits' || kind === 'series' || kind === 'entitlements'}><summary>作品资料与版权信息</summary><Summary item={item} /></details>
      {kind === 'works' && item.sale_type !== 'free' && <WorkPreviewLink id={Number(item.id)} />}
      {typeof item.asset_id === 'number' && <FileLink id={item.asset_id} label="视频预览" purpose="original" video />}
      {kind === 'works' && typeof item.cover_asset_id === 'number' && <FileLink id={item.cover_asset_id} label="作品封面" />}{typeof item.certificate_asset_id === 'number' && <FileLink id={item.certificate_asset_id} label="权属证明／登记证书" />}
      {Array.isArray(item.evidence_ids) && item.evidence_ids.map(id => <FileLink key={Number(id)} id={Number(id)} label="证明材料" />)}
      {kind === 'series' && Array.isArray(item.episodes) && <div className="market-ops-episodes"><h4>分集材料</h4>{item.episodes.map((episode: Item) => <section key={episode.id}><strong>第 {String(episode.episode_no)} 集 · {String(episode.title || '')}</strong>{typeof episode.asset_id === 'number' && <FileLink id={episode.asset_id} label="分集视频" purpose="original" video />}{typeof episode.cover_asset_id === 'number' && <FileLink id={episode.cover_asset_id} label="分集封面" />}{typeof episode.certificate_asset_id === 'number' && <FileLink id={episode.certificate_asset_id} label="分集版权证明" />}{Array.isArray(episode.evidence_ids) && episode.evidence_ids.map(value => <FileLink key={Number(value)} id={Number(value)} label="分集证明材料" />)}<AuthorizationLink details={episode.details} /></section>)}</div>}
      <AuthorizationLink details={item.details} />{kind === 'submissions' && <p>投稿快照：{JSON.stringify(item.snapshot)}</p>}
      {((kind === 'recruits' || kind === 'series' || kind === 'works') && item.audit_status === 'pending') && <form className="market-ops-review" onSubmit={e => review(e, item)}><h3>审核处理</h3><p>查看内容和版权材料后，给出本次审核结论。</p>{(kind === 'works' || kind === 'series') && <div className="grid gap-3"><section><h3>内容检查</h3><label><input name="checks" value="content" type="checkbox" /> 已检查视频、封面、标题和简介</label></section><section><h3>版权检查</h3><label><input name="checks" value="copyright" type="checkbox" /> 已检查授权函或证书编号、证明材料及版权声明</label></section></div>}<label className="block">审核依据及原因<textarea name="reason" required maxLength={3000} className="block w-full rounded border bg-background p-2" /></label><div className="flex gap-3"><button type="submit" value="approved" className="market-ops-approve">通过该版本</button><button type="submit" value="rejected" className="market-ops-reject">驳回该版本</button></div></form>}
      {kind === 'reports' && <form onSubmit={e => { e.preventDefault(); const form = new FormData(e.currentTarget); if (window.confirm('确认记录举报处理结果？')) void action(`/reports/${item.id}/resolve`, { resolution: form.get('resolution') }) }}><textarea name="resolution" aria-label="处理结果" required maxLength={3000} className="block w-full rounded border bg-background p-2" /><button type="submit">记录处理结果</button><button type="button" onClick={() => { const reason = window.prompt('临时限制该举报对象的依据和原因'); if (reason) void action(`/block/${item.target_type}/${item.target_id}`, { blocked: true, reason }) }}>临时限制举报对象</button></form>}
      {kind === 'payouts' && <PayoutStatusForm item={item} onSaved={() => setRefresh(v => v + 1)} />}
      {['works', 'recruits', 'submissions'].includes(kind) && <button type="button" onClick={() => { const reason = window.prompt(`${item.blocked ? '解除限制' : '限制播放和咨询'}的原因`); if (reason) void action(`/block/${({ works: 'work', recruits: 'recruit', submissions: 'submission' } as Record<string, string>)[kind]}/${item.id}`, { blocked: !item.blocked, reason }) }}>{item.blocked ? '解除限制（不自动上架）' : '封禁／临时下架'}</button>}
    </article>)}{!loading && !error && listing?.items.length === 0 && <div className="market-ops-empty"><Inbox size={36} /><h3>暂无{tabs[kind]}记录</h3><p>新的业务记录会显示在这里，可切换状态查看。</p></div>}{listing && <PaginationBar page={page} pageSize={listing.meta.page_size} total={listing.meta.total} onPageChange={setPage} />}</>}
  </div>
}

// 二维码同样必须走独立 OSS；无配置时清晰报错，不生成假图片。
function SupportForm() {
  const [settings, setSettings] = useState<Item | null>(null)
  const [qr, setQr] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { request<Item>('/api/admin/marketplace/support').then(value => { setSettings(value); setQr(typeof value.qr_asset_id === 'number' ? value.qr_asset_id : null) }).catch(e => setMessage(e.message)) }, [])
  // 小二维码也使用同一分片协议，上传完成后需要 Worker 校验。
  async function uploadQr(file?: File) {
    if (!file) return; setBusy(true)
    try {
      const session = await request<{ id: number; asset_id: number; part_size: number }>('/api/marketplace/uploads', 'POST', { filename: file.name, size_bytes: file.size, kind: 'qr' })
      for (let start = 0, number = 1; start < file.size; start += session.part_size, number++) await request(`/api/marketplace/uploads/${session.id}/parts/${number}`, 'PUT', file.slice(start, start + session.part_size))
      await request(`/api/marketplace/uploads/${session.id}/complete`, 'POST'); setQr(session.asset_id); setMessage('二维码上传完成，请保存客服配置。')
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }
  // 后台保存仍校验二维码属于管理员且状态 ready。
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = new FormData(e.currentTarget); setBusy(true)
    try { await request('/api/admin/marketplace/support', 'PUT', { name: form.get('name'), wechat: form.get('wechat'), hours: form.get('hours'), enabled: form.get('enabled') === 'on', qr_asset_id: qr }); setMessage('客服配置已保存') } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }
  if (!settings) return <p>{message || '正在加载客服配置…'}</p>
  return <form className="market-ops-support" onSubmit={save}><label className="block">客服名称<input name="name" required maxLength={80} defaultValue={String(settings.name ?? '平台客服')} className="block w-full rounded border bg-background p-2" /></label><label className="block">微信号<input name="wechat" maxLength={100} defaultValue={String(settings.wechat ?? '')} className="block w-full rounded border bg-background p-2" /></label><label className="block">服务时间<input name="hours" maxLength={100} defaultValue={String(settings.hours ?? '')} className="block w-full rounded border bg-background p-2" /></label><label className="block">上传真实客服二维码<input type="file" accept=".png,.jpg,.jpeg,.webp" disabled={busy} onChange={e => uploadQr(e.target.files?.[0])} /></label><p>二维码文件编号：{qr ?? '未设置'}</p>{qr && <FileLink id={qr} label="检查二维码" />}<label className="block"><input name="enabled" type="checkbox" defaultChecked={Boolean(settings.enabled)} /> 启用平台客服</label><button type="submit" disabled={busy}>保存客服配置</button><p role="status">{message}</p></form>
}

function RevenueConfigForm() {
  const [value, setValue] = useState<Item | null>(null)
  const [share, setShare] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [message, setMessage] = useState('正在加载平台分成配置…')
  const [busy, setBusy] = useState(false)
  useEffect(() => { request<Item>('/api/admin/marketplace/revenue-config').then(item => { setValue(item); setShare(String(item.platform_share_bps ?? '')); setEnabled(Boolean(item.enabled)); setMessage('') }).catch(e => setMessage(e.message)) }, [])
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setMessage('')
    try {
      const bps = Number(share)
      if (!Number.isInteger(bps) || bps < 0 || bps > 10000) throw new Error('平台分成必须是 0 至 10000 的整数（万分比）')
      const result = await request<Item>('/api/admin/marketplace/revenue-config', 'PUT', { version: value?.version ?? 0, platform_share_bps: bps, enabled })
      setValue(result); setMessage('平台分成配置已保存；未配置前不能提交平台投放作品。')
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="market-ops-support" onSubmit={save}><h2>平台投放收益分成</h2><p>按万分比填写平台抽成，例如 2000 表示平台 20%，创作者自动获得剩余 80%。每个作品可以单独覆盖此默认比例。</p><label className="block">平台分成（万分比）<input type="number" min="0" max="10000" step="1" value={share} onChange={e => setShare(e.target.value)} required className="block w-full rounded border bg-background p-2" /></label><label className="block"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> 启用平台投放收益</label><button type="submit" disabled={busy || !value}>{busy ? '保存中…' : '保存配置'}</button><p role="status">{message}</p></form>
}

function PayoutCreateForm({ onSaved }: { onSaved: () => void }) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setMessage('')
    const formElement = e.currentTarget; const form = new FormData(formElement)
    try {
      await request('/api/admin/marketplace/payouts', 'POST', { owner_id: Number(form.get('owner_id')), amount_fen: Math.round(Number(form.get('amount')) * 100), status: form.get('status'), note: form.get('note') })
      formElement.reset(); setMessage('打款记录已保存'); onSaved()
    } catch (err) { setMessage((err as Error).message) } finally { setBusy(false) }
  }
  return <form className="market-ops-support" onSubmit={save}><h3>记录人工打款</h3><div className="pf-market-field-row"><label>创作者用户编号<input name="owner_id" type="number" min="1" required /></label><label>金额（元）<input name="amount" type="number" min="0.01" step="0.01" required /></label></div><label>状态<select name="status" defaultValue="paid"><option value="pending">待结算</option><option value="paid">已打款</option><option value="failed">打款失败</option></select></label><label>备注<textarea name="note" maxLength={3000} /></label><button type="submit" disabled={busy}>{busy ? '保存中…' : '保存打款记录'}</button><p role="status">{message}</p></form>
}








