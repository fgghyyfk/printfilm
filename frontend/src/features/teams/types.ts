/** 团队真实数据模型，字符串 ID 仅用于路由与表单控件。 */
export type TeamRole = 'owner' | 'admin' | 'member'
export type TeamTab = 'overview' | 'projects' | 'assets' | 'members' | 'billing' | 'settings'
export type TeamProjectStatus = 'draft' | 'producing' | 'completed'
export type Page<T> = { items: T[]; meta: { total: number; page: number; page_size: number } }
export type TeamMember = { id: string; name: string; email: string; avatarUrl?: string; role: TeamRole; color: string; monthlyLimitFen: number | null; spentFen: number | null; reservedFen: number | null; joinedAt: string }
export type TeamInvitation = { id: string; email: string; createdAt: string; expiresAt: string; status: string; acceptUrl?: string; emailSent?: boolean }
export type TeamProject = { id: string; title: string; description: string; genre: string; cover: string; status: TeamProjectStatus; episodes: number; completedEpisodes: number; updatedAt: string; collaborators: string[]; editingBy?: string }
export type TeamAsset = { id: string; name: string; type: 'character' | 'scene' | 'prop' | 'voice'; cover: string; url: string; projectId: string; description: string; updatedAt: string; assetType?: string; params?: Record<string, unknown> }
export type TeamCharge = { id: string; memberId: string; projectId: string; label: string; model: string; amountFen: number; createdAt: string }
export type TeamActivity = { id: string; memberId: string; memberName?: string; action: string; subject: string; createdAt: string }
export type UsageSummary = { team_monthly_limit_fen?: number | null; billing_source: "owner_personal"; balance_fen?: number; frozen_fen?: number; spent_fen: number; reserved_fen: number; monthly_limit_fen: number | null; period: string; billing_enabled: boolean }
export type Team = { id: string; name: string; description: string; color: string; createdAt: string; myRole: TeamRole; viewerId: string; memberCount: number; projectCount: number; assetCount: number; balanceFen: number; frozenFen: number; members: TeamMember[]; invitations: TeamInvitation[]; projects: TeamProject[]; assets: TeamAsset[]; charges: TeamCharge[]; activities: TeamActivity[]; usage?: UsageSummary }
/** 兼容原有展示组件名称，数据均来自真实服务端。 */
export type PreviewTeam = Team
export type PreviewMember = TeamMember
export type PreviewInvitation = TeamInvitation
export type PreviewProject = TeamProject
export type PreviewAsset = TeamAsset
export type PreviewCharge = TeamCharge
export type PreviewActivity = TeamActivity
export const TEAM_ROLE_LABELS = { owner: '所有者', admin: '管理员', member: '成员' }
export const TEAM_COLORS = ['#435c4b', '#766492', '#48687b', '#a06e51', '#45464c']
/** 角色只取真实成员关系。 */
export function viewerRole(team: Team): TeamRole { return team.myRole }
