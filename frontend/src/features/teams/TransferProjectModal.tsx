import { useEffect, useState, type FormEvent } from 'react'
import { ArrowRight, FolderInput, LockKeyhole } from 'lucide-react'
import Button from '../../components/ui/Button'
import Modal from '../../components/ui/Modal'
import { teamsApi } from '../../api/teams'
import { resolveDramaMediaUrl } from '../../api/drama'
import { useTeamPreview } from './useTeamPreview'
import { useTeamPage } from './useTeamPage'
import { TeamPagination } from './TeamPagination'
import type { Team } from './types'

/** 真实个人项目整体转入；服务端二次校验任务、占用和所有权。 */
export default function TransferProjectModal({ open, onClose, team }: { open: boolean; onClose: () => void; team: Team }) {
  /* selectedId 选择项目；accepted 归属确认；busy/error 提交反馈；page 服务端分页。 */
  const [selectedId, setSelectedId] = useState('')
  const [accepted, setAccepted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const { loadTeam } = useTeamPreview()
  const listing = useTeamPage(`${team.id}:transfer:${open}:${page}`, (signal) => open ? teamsApi.transferable(team.id, { page, page_size: 12 }, signal) : Promise.resolve({ items: [], meta: { total: 0, page: 1, page_size: 12 } }))
  useEffect(() => { if (open) { setSelectedId(''); setAccepted(false); setError(''); setPage(1) } }, [open, team.id])
  /** 用户明确确认后执行真实归属变更；失败时保留选项。 */
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!selectedId || !accepted || busy) return
    setBusy(true); setError('')
    try { await teamsApi.transferProject(team.id, selectedId); await loadTeam(team.id); onClose() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '转入失败，请重试'); listing.retry() }
    finally { setBusy(false) }
  }
  return <Modal open={open} onClose={onClose} dismissible={!busy} title="转入个人项目" size="lg"><form className="pf-team-form" onSubmit={submit}><div className="pf-team-form-intro"><span className="pf-team-mark"><FolderInput size={23} /></span><div><strong>把已有故事带给团队</strong><p>选择你拥有的个人项目，整体转入团队。</p></div></div>{listing.loading ? <p role="status">正在检查可转入项目…</p> : listing.error ? <div className="pf-team-alert" role="alert">{listing.error}<Button onClick={listing.retry}>重试</Button></div> : listing.items.length ? <div className="pf-team-transfer-list" role="group" aria-label="选择个人项目">{listing.items.map((project) => <label key={project.id} className={`pf-team-transfer-item${selectedId === project.id ? ' is-selected' : ''}`}><input type="radio" name="transfer-project" value={project.id} checked={selectedId === project.id} disabled={!project.transferable || busy} onChange={() => setSelectedId(project.id)} />{project.cover && <img src={resolveDramaMediaUrl(project.cover)} alt="" />}<span><strong>{project.title}</strong><small>{project.episodes} 集 · 个人项目</small></span><em>{project.transferable ? '可转入' : <><LockKeyhole size={12} />{project.reason || '暂不可转入'}</>}</em></label>)}</div> : <div className="pf-team-empty">你还没有可列出的个人项目。</div>}<TeamPagination page={page} total={listing.meta.total} onChange={setPage} /><div className="pf-team-transfer-target"><span>个人空间</span><ArrowRight size={16} /><strong>{team.name}</strong></div><div className="pf-team-form-note">项目、分集和资产整体归团队，所有成员均可访问；历史个人消费保持原归属。转入后不能转回个人空间。</div><label className="pf-team-transfer-confirm"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} required disabled={busy} />我了解并确认项目归属将永久变更</label>{error && <p className="pf-team-alert" role="alert">{error}</p>}<div className="pf-team-modal-actions"><Button onClick={onClose} disabled={busy}>取消</Button><Button variant="lime" type="submit" disabled={!selectedId || !accepted || busy}>{busy ? '正在转入…' : '确认转入团队'}</Button></div></form></Modal>
}
