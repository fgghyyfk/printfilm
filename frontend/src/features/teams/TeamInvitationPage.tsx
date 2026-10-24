import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import { teamsApi } from '../../api/teams'
import { useTeamPreview } from './useTeamPreview'
import RequireAuth from '../../pages/drama/RequireAuth'
import './teams.css'

/** 加入页面要求真实登录，邮箱匹配由服务器再次校验。 */
export default function TeamInvitationPage() { return <RequireAuth><InvitationInner /></RequireAuth> }
/** token 不存储、不输出到日志，仅通过 POST 请求体传递。 */
function InvitationInner() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const navigate = useNavigate()
  const { reload, selectTeam } = useTeamPreview()
  /* invitation 检查结果；loading/busy/error 校验与提交反馈。 */
  const [invitation, setInvitation] = useState<{ team_name: string; email: string; expires_at: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { let active = true; setLoading(true); setError(''); setInvitation(null); if (!token) { setError('邀请链接缺少令牌'); setLoading(false); return }; void teamsApi.inspectInvitation(token).then((data) => { if (active) setInvitation(data) }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '邀请已失效') }).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [token])
  /** 明确点击后消费邀请，加入成功替换 URL 清除令牌。 */
  async function accept() { setBusy(true); setError(''); try { const team = await teamsApi.acceptInvitation(token); selectTeam(team.id); navigate(`/teams/${team.id}`, { replace: true }); void reload() } catch (cause) { setError(cause instanceof Error ? cause.message : '加入失败，请重试') } finally { setBusy(false) } }
  return <AppShell active="team"><section className="pf-team-panel pf-team-empty"><h1>加入创作团队</h1>{loading ? <p role="status">正在验证邀请…</p> : invitation ? <><h2>{invitation.team_name}</h2><p>邀请邮箱：{invitation.email}</p><p className="pf-team-muted">有效期至 {new Date(invitation.expires_at).toLocaleString('zh-CN')}</p><Button variant="lime" onClick={accept} disabled={busy}>{busy ? '正在加入…' : '接受邀请并加入'}</Button></> : null}{error && <p className="pf-team-alert" role="alert">{error}</p>}<Button to="/teams">返回我的团队</Button></section></AppShell>
}
