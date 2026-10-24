import { recordDramaResponse } from '../features/teams/editLocks'
import { createJsonRequester, requestResponse } from './client'
import type { Page, Team, TeamMember, TeamInvitation, TeamProject, TeamAsset, TeamCharge, TeamActivity, UsageSummary } from '../features/teams/types'

/* 统一客户端与接口数据转换；后端始终验证权限。 */
const request = createJsonRequester()
type Row = Record<string, any>
export type TeamQuery = Record<string, string | number | undefined>
/** 生成服务器分页与筛选参数。 */
function query(values: TeamQuery = {}): string { const params = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) }); return `?${params}` }
/** 逐条转换分页响应，保留服务端总数。 */
async function page<T>(path: string, values: TeamQuery, map: (row: Row) => T, signal?: AbortSignal): Promise<Page<T>> { const data = await request<Page<Row>>(path + query(values), { signal }); return { ...data, items: data.items.map(map) } }
/** 团队概要不预装任何虚构内容。 */
export function mapTeam(row: Row): Team { return { id: String(row.id), name: row.name, description: row.description || '', color: row.color || '#435c4b', createdAt: row.created_at, myRole: row.my_role, viewerId: String(row.viewer_id), memberCount: row.member_count || 0, projectCount: row.project_count || 0, assetCount: row.asset_count || 0, balanceFen: 0, frozenFen: 0, members: [], invitations: [], projects: [], assets: [], charges: [], activities: [] } }
/** 保留被服务端隐藏的财务 null 值。 */
function member(row: Row): TeamMember { return { id: String(row.id), name: row.nickname || row.email, email: row.email, avatarUrl: row.avatar_url, role: row.role, color: '#e4eadc', monthlyLimitFen: row.monthly_limit_fen, spentFen: row.spent_fen, reservedFen: row.reserved_fen, joinedAt: row.joined_at } }
/** 转换真实项目卡片。 */
function project(row: Row): TeamProject { return { id: String(row.id), title: row.title, description: row.description || '', genre: row.genre || '漫剧', cover: row.cover || '', status: row.status || 'draft', episodes: row.episodes || 0, completedEpisodes: row.completed_episodes || 0, updatedAt: row.updated_at, collaborators: (row.collaborators || []).map(String), editingBy: row.editing_by ? String(row.editing_by) : undefined } }
/** 转换真实资产。 */
function asset(row: Row): TeamAsset { return { id: String(row.id), name: row.name || '未命名资产', type: row.type, cover: row.cover || '', url: row.url || '', projectId: String(row.project_id), description: row.description || '', updatedAt: row.updated_at } }
/** 邀请列表不包含可重用令牌。 */
function invitation(row: Row): TeamInvitation { return { id: String(row.id), email: row.email, status: row.status, createdAt: row.created_at, expiresAt: row.expires_at } }
/** 转换真实消费记录。 */
function charge(row: Row): TeamCharge { return { id: String(row.id), memberId: String(row.member_id), projectId: String(row.project_id), label: row.label, model: row.model, amountFen: row.amount_fen, createdAt: row.created_at } }
/** 转换协作记录。 */
function activity(row: Row): TeamActivity { return { id: String(row.id), memberId: String(row.actor_user_id || row.member_id || row.user_id), memberName: row.actor_nickname, action: row.action, subject: row.subject || '', createdAt: row.created_at } }
/** 创建/重发时只在内存返回真实加入链接。 */
async function inviteResult(path: string, body?: unknown): Promise<TeamInvitation> { const data = await request<Row>(path, { method: 'POST', body: JSON.stringify(body || {}) }); return { ...invitation(data.invitation), acceptUrl: data.accept_url, emailSent: data.email_sent } }
/** 团队服务接口，复用统一认证与错误处理。 */
export const teamsApi = {
  delete: (id: string) => requestResponse(`/api/teams/${id}`, { method: 'DELETE' }),
  list: (values: TeamQuery = {}, signal?: AbortSignal) => page('/api/teams', values, mapTeam, signal),
  get: async (id: string, signal?: AbortSignal) => mapTeam(await request<Row>(`/api/teams/${id}`, { signal })),
  create: async (body: { name: string; description: string; color: string }) => mapTeam(await request<Row>('/api/teams', { method: 'POST', body: JSON.stringify(body) })),
  update: (id: string, body: unknown) => request(`/api/teams/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  members: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/members`, values, member, signal),
  invitations: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/invitations`, values, invitation, signal),
  invite: (id: string, email: string) => inviteResult(`/api/teams/${id}/invitations`, { email }),
  resend: (id: string, invitationId: string) => inviteResult(`/api/teams/${id}/invitations/${invitationId}/resend`),
  revoke: (id: string, invitationId: string) => request(`/api/teams/${id}/invitations/${invitationId}/revoke`, { method: 'POST' }),
  member: (id: string, memberId: string, body: unknown) => request(`/api/teams/${id}/members/${memberId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  removeMember: (id: string, memberId: string) => request(`/api/teams/${id}/members/${memberId}`, { method: 'DELETE' }),
  leave: (id: string) => request(`/api/teams/${id}/leave`, { method: 'POST' }),
  transfer: (id: string, userId: string) => request(`/api/teams/${id}/transfer-ownership`, { method: 'POST', body: JSON.stringify({ user_id: Number(userId) }) }),
  projects: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/projects`, values, project, signal),
  createProject: (id: string, body: unknown) => request<{ id: number }>(`/api/teams/${id}/projects`, { method: 'POST', body: JSON.stringify(body) }),
  transferable: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/transferable-projects`, values, (row) => ({ ...project(row), transferable: Boolean(row.transferable), reason: String(row.reason || '') }), signal),
  transferProject: async (id: string, projectId: string) => { const result = await request(`/api/teams/${id}/projects/${projectId}/transfer`, { method: 'POST' }); recordDramaResponse(`/api/drama/projects/${projectId}`, result); return result },
  copyAsset: (id: string, assetId: number, projectId: number, headers: HeadersInit) => request<import('./drama').DramaAsset>(`/api/teams/${id}/assets/${assetId}/copy`, { method: 'POST', headers, body: JSON.stringify({ project_id: projectId }) }),
  assets: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/assets`, values, asset, signal),
  activities: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/activities`, values, activity, signal),
  summary: (id: string, signal?: AbortSignal) => request<UsageSummary>(`/api/teams/${id}/usage/summary`, { signal }),
  charges: (id: string, values: TeamQuery = {}, signal?: AbortSignal) => page(`/api/teams/${id}/usage/records`, values, charge, signal),
  inspectInvitation: (token: string) => request<{ team_name: string; email: string; expires_at: string }>('/api/team-invitations/inspect', { method: 'POST', body: JSON.stringify({ token }) }),
  acceptInvitation: async (token: string) => mapTeam(await request<Row>('/api/team-invitations/accept', { method: 'POST', body: JSON.stringify({ token }) })),
  order: (id: string, trade: string) => request<{ status: string }>(`/api/teams/${id}/orders/${encodeURIComponent(trade)}`),
  closeOrder: (id: string, trade: string) => request(`/api/teams/${id}/orders/${encodeURIComponent(trade)}/close`, { method: 'POST' }),
}
