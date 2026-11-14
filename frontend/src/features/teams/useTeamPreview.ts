import { createContext, useContext } from 'react'
import type { Team, TeamInvitation } from './types'

/** 真实团队缓存与异步管理动作，分页列表由各页面请求。 */
export type TeamContextValue = {
  teams: Team[]; activeTeamId: string | null; loading: boolean; error: string; revision: number
  selectTeam: (id: string | null) => void
  reload: () => Promise<void>
  loadTeam: (id: string) => Promise<Team>
  createTeam: (input: { name: string; description: string; color: string }) => Promise<string>
  updateTeam: (id: string, patch: Pick<Team, 'name' | 'description' | 'color'>) => Promise<void>
  inviteMember: (id: string, email: string) => Promise<TeamInvitation>
  revokeInvitation: (id: string, invitationId: string) => Promise<void>
  updateMember: (id: string, memberId: string, patch: { role?: 'admin' | 'member'; monthlyLimitFen?: number | null }) => Promise<void>
  removeMember: (id: string, memberId: string) => Promise<void>
  createProject: (id: string, input: { title: string; description: string; source: string; episodes: number }) => Promise<string>
  transferOwnership: (id: string, memberId: string) => Promise<void>
  deleteTeam: (id: string) => Promise<void>
  leaveTeam: (id: string) => Promise<void>
}
/** 与组件模块分离，保证热更新和上下文身份一致。 */
export const TeamContext = createContext<TeamContextValue | null>(null)
/** 现有展示组件共用真实团队服务。 */
export function useTeamPreview(): TeamContextValue { const context = useContext(TeamContext); if (!context) throw new Error('团队页面需要 TeamProvider'); return context }
