import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { teamsApi } from '../../api/teams'
import { USER_UPDATED_EVENT } from '../../lib/userEvents'
import { TeamContext, type TeamContextValue } from './useTeamPreview'
import { setWorkspaceTeam, subscribeWorkspace, workspaceScope } from './workspaceScope'
import type { Team } from './types'

/** 真实团队缓存，账号代次保护避免迟到响应恢复上一账号的权限和钱包。 */
export function TeamPreviewProvider({ children }: { children: ReactNode }) {
  /* teams 概要缓存；activeTeamId 当前空间；loading/error 查询反馈；revision 列表失效代次。 */
  const [teams, setTeams] = useState<Team[]>([])
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const authEpoch = useRef(0)
  const teamEpochs = useRef(new Map<string, number>())
  /** 捕获真实 token 和账号代次，所有异步状态写回前必须验证。 */
  const capture = useCallback(() => { const epoch = authEpoch.current; const token = localStorage.getItem('token'); return () => epoch === authEpoch.current && token === localStorage.getItem('token') }, [])
  /** 账号/成员关系发生变化后拒绝使用旧响应继续导航或操作。 */
  const requireCurrent = useCallback((valid: () => boolean) => { if (!valid()) throw new DOMException('登录或团队状态已变化', 'AbortError') }, [])
  /** 更新单个真实概要。 */
  const cacheTeam = useCallback((team: Team) => { setTeams((old) => [...old.filter((item) => item.id !== team.id), team]) }, [])
  /** 选择器读取一页团队；目录页另行服务端分页。 */
  const reload = useCallback(async () => {
    const valid = capture()
    if (!localStorage.getItem('token')) { setTeams([]); setLoading(false); return }
    setLoading(true); setError('')
    try {
      const result = await teamsApi.list({ page: 1, page_size: 50 })
      if (valid()) setTeams((old) => result.items.map((team) => {
        const cached = old.find((item) => item.id === team.id && item.viewerId === team.viewerId && item.myRole === team.myRole)
        return cached ? { ...cached, name: team.name, description: team.description, color: team.color, memberCount: team.memberCount, projectCount: team.projectCount, assetCount: team.assetCount } : team
      }))
    }
    catch (cause) { if (valid() && !(cause instanceof DOMException && cause.name === 'AbortError')) setError(cause instanceof Error ? cause.message : '团队加载失败') }
    finally { if (valid()) setLoading(false) }
  }, [capture])
  /** 概览只读取首屏；退出团队或切账号后的响应不可重新加入缓存。 */
  const loadTeam = useCallback(async (id: string) => {
    const authValid = capture(); const generation = teamEpochs.current.get(id) || 0
    const valid = () => authValid() && generation === (teamEpochs.current.get(id) || 0)
    const team = await teamsApi.get(id); requireCurrent(valid)
    const [projects, members, activities, summary] = await Promise.all([teamsApi.projects(id, { page: 1, page_size: 3 }), teamsApi.members(id, { page: 1, page_size: 10 }), teamsApi.activities(id, { page: 1, page_size: 5 }), teamsApi.summary(id)])
    requireCurrent(valid)
    const result = { ...team, projects: projects.items, members: members.items, activities: activities.items, usage: summary, balanceFen: summary.balance_fen ?? 0, frozenFen: summary.frozen_fen ?? 0 }
    cacheTeam(result); setRevision((value) => value + 1)
    return result
  }, [capture, cacheTeam, requireCurrent])
  /** 唯一范围来源同时供路由和真实项目响应使用。 */
  const selectTeam = useCallback((id: string | null) => { setWorkspaceTeam(id === null ? null : Number(id)); setActiveTeamId(id) }, [])
  useEffect(() => subscribeWorkspace(() => { setActiveTeamId(workspaceScope.teamId === null ? null : String(workspaceScope.teamId)) }), [])
  useEffect(() => {
    void reload()
    /** 登录变化立即隔离旧身份；旧请求即使已收到响应也不能再写回缓存。 */
    const refresh = () => { authEpoch.current += 1; teamEpochs.current.clear(); setTeams([]); setActiveTeamId(null); setWorkspaceTeam(null, true); void reload() }
    window.addEventListener(USER_UPDATED_EVENT, refresh)
    return () => window.removeEventListener(USER_UPDATED_EVENT, refresh)
  }, [reload])
  useEffect(() => { if (activeTeamId) void loadTeam(activeTeamId).catch(() => undefined) }, [activeTeamId, loadTeam])
  /** 管理动作成功后刷新真实数据；刷新失败不把已完成的变更报告成失败。 */
  const mutate = useCallback(async <T,>(id: string, action: () => Promise<T>): Promise<T> => {
    const valid = capture(); const result = await action(); requireCurrent(valid)
    setRevision((value) => value + 1)
    await loadTeam(id).catch((cause) => { if (valid()) setError(cause instanceof Error ? `操作已完成，刷新失败：${cause.message}` : '操作已完成，请重新加载团队资料') })
    requireCurrent(valid)
    return result
  }, [capture, loadTeam, requireCurrent])
  const value = useMemo<TeamContextValue>(() => ({
    teams, activeTeamId, loading, error, revision, selectTeam, reload, loadTeam,
    createTeam: async (input) => { const valid = capture(); const team = await teamsApi.create(input); requireCurrent(valid); cacheTeam(team); setRevision((n) => n + 1); selectTeam(team.id); return team.id },
    updateTeam: async (id, patch) => { await mutate(id, () => teamsApi.update(id, patch)) },
    inviteMember: (id, email) => mutate(id, () => teamsApi.invite(id, email)),
    revokeInvitation: async (id, inviteId) => { await mutate(id, () => teamsApi.revoke(id, inviteId)) },
    updateMember: async (id, memberId, patch) => { await mutate(id, () => teamsApi.member(id, memberId, { ...(patch.role ? { role: patch.role } : {}), ...(patch.monthlyLimitFen !== undefined ? { monthly_limit_fen: patch.monthlyLimitFen } : {}) })) },
    removeMember: async (id, memberId) => { await mutate(id, () => teamsApi.removeMember(id, memberId)) },
    createProject: async (id, input) => { const project = await mutate(id, () => teamsApi.createProject(id, { title: input.title, description: input.description, source: input.source, episode_count: input.episodes, workflow: 'script' })); return String(project.id) },
    transferOwnership: async (id, memberId) => { await mutate(id, () => teamsApi.transfer(id, memberId)) },
    deleteTeam: async (id) => { const valid = capture(); await teamsApi.delete(id); requireCurrent(valid); teamEpochs.current.set(id, (teamEpochs.current.get(id) || 0) + 1); setTeams((old) => old.filter((team) => team.id !== id)); selectTeam(null); setRevision((n) => n + 1) },
    leaveTeam: async (id) => { const valid = capture(); await teamsApi.leave(id); requireCurrent(valid); teamEpochs.current.set(id, (teamEpochs.current.get(id) || 0) + 1); setTeams((old) => old.filter((team) => team.id !== id)); selectTeam(null); setRevision((n) => n + 1) },
  }), [teams, activeTeamId, loading, error, revision, selectTeam, reload, loadTeam, cacheTeam, capture, requireCurrent, mutate])
  return <TeamContext.Provider value={value}>{children}</TeamContext.Provider>
}
