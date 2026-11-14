import { createJsonRequester } from '../../api/client'
import { setWorkspaceTeam, subscribeWorkspace, workspaceScope } from './workspaceScope'
import { renewedLease, isSameLease, type EditLease } from './leaseIdentity'

export type EditResource = 'structure' | 'script' | 'episode' | 'asset' | 'canvas'
export type EditTarget = { projectId: number; type: EditResource; resourceId: number }
type Lease = EditLease
type Held = { lease: Lease; target: EditTarget; teamId: number; refs: number; timer: ReturnType<typeof setInterval>; lost?: string }
type Row = Record<string, any>
/* 注册表记录真实资源归属，不从当前选中团队猜测项目所属。 */
const request = createJsonRequester()
const projectTeams = new Map<number, number | null>()
const assets = new Map<number, number>()
const episodes = new Map<number, { projectId: number; number: number }>()
const fragments = new Map<number, number>()
const held = new Map<string, Held>()
const pending = new Map<string, Promise<Held>>()
const listeners = new Set<() => void>()
export const editSessionId = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16)).join('-')
/** 资源锁键与后端一致，分集正文和分镜共用集号。 */
function key(target: EditTarget): string { return `${target.projectId}:${target.type}:${target.resourceId}` }
/** 通知页面占用状态变化。 */
function emit(): void { listeners.forEach((listener) => listener()) }
/** 订阅占用失效等事件。 */
export function subscribeEditLocks(listener: () => void): () => void { listeners.add(listener); return () => { listeners.delete(listener) } }
/** 获取已持有资源的失效原因。 */
export function editLockError(target: EditTarget): string { return held.get(key(target))?.lost || '' }
/** 显式重试只清理本标签页已失效的租约，不强制接管其他成员。 */
export async function releaseLostEditLock(target: EditTarget): Promise<void> {
  const entry = held.get(key(target))
  if (entry?.lost) await releaseEditLock(target, true, entry.lease.token)
}
/** 注册读取响应内的实际资源关系。 */
export function recordDramaResponse(path: string, data: unknown, requestEpoch?: number): void {
  if (requestEpoch !== undefined && requestEpoch !== workspaceScope.epoch) return
  const rows = Array.isArray(data) ? data : [data]
  for (const row of rows as Row[]) {
    if (!row || typeof row !== 'object') continue
    if (row.user_id !== undefined && row.id && (row.script !== undefined || row.asset_count !== undefined)) {
      projectTeams.set(row.id, row.team_id ?? null)
      const visible = typeof window === 'undefined' ? 0 : Number(window.location.pathname.match(/^\/drama\/projects\/(\d+)/)?.[1] || 0)
      if (requestEpoch === workspaceScope.epoch && visible === row.id && /\/projects\/\d+(\?|$)/.test(path)) setWorkspaceTeam(row.team_id ?? null)
    }
    if (row.id && row.project_id && ('asset_type' in row || path.includes('/assets'))) assets.set(row.id, row.project_id)
    if (row.id && row.project_id && ('fragments' in row || path.includes('/episodes'))) {
      episodes.set(row.id, { projectId: row.project_id, number: Number(row.params?.episodeNumber || row.id) })
      for (const fragment of row.fragments || []) fragments.set(fragment.id, row.id)
    }
  }
}
/** 按资源自身的服务端归属确定团队，个人项目永不取得团队锁。 */
export async function projectTeam(projectId: number): Promise<number | null> {
  if (projectTeams.has(projectId)) return projectTeams.get(projectId) ?? null
  const epoch = workspaceScope.epoch
  const token = localStorage.getItem('token')
  const project = await request<Row>(`/api/drama/projects/${projectId}`)
  if (epoch !== workspaceScope.epoch || token !== localStorage.getItem('token')) throw new DOMException('空间或登录身份已变化', 'AbortError')
  recordDramaResponse(`/api/drama/projects/${projectId}`, project)
  return project.team_id ?? null
}
/** 只允许当前可见项目同步空间；旧后台请求不改变导航范围。 */
export async function syncProjectWorkspace(projectId: number): Promise<void> {
  const epoch = workspaceScope.epoch
  const teamId = await projectTeam(projectId)
  const visible = typeof window === 'undefined' ? 0 : Number(window.location.pathname.match(/^\/drama\/projects\/(\d+)/)?.[1] || 0)
  if (epoch === workspaceScope.epoch && visible === projectId) setWorkspaceTeam(teamId)
}
/** 取得或复用同一标签页的锁，续租失败后锁定为只读。 */
export async function holdEditLock(target: EditTarget, force = false): Promise<string | null> {
  const epoch = workspaceScope.epoch
  const authToken = localStorage.getItem('token')
  const teamId = await projectTeam(target.projectId)
  if (epoch !== workspaceScope.epoch || authToken !== localStorage.getItem('token')) throw new DOMException('空间或登录身份已变化', 'AbortError')
  if (teamId === null) return null
  const id = key(target)
  const existing = held.get(id)
  if (existing?.lost && !force) throw new Error(existing.lost)
  if (existing && !existing.lost && !force) { existing.refs += 1; return existing.lease.token }
  let promise = pending.get(id)
  if (!promise || force) {
    promise = (async () => {
      const lease = await request<Lease>(`/api/teams/${teamId}/edit-locks/acquire`, { method: 'POST', body: JSON.stringify({ project_id: target.projectId, resource_type: target.type, resource_id: target.resourceId, session_id: editSessionId, force }) })
      if (epoch !== workspaceScope.epoch || authToken !== localStorage.getItem('token')) {
        void request(`/api/teams/${teamId}/edit-locks/release`, { method: 'POST', body: JSON.stringify({ token: lease.token, session_id: editSessionId }) }).catch(() => undefined)
        throw new DOMException('空间或登录身份已变化', 'AbortError')
      }
      if (existing) clearInterval(existing.timer)
      const entry: Held = { lease, teamId, target, refs: 0, timer: setInterval(() => {
        void request<Omit<Lease, 'token'>>(`/api/teams/${teamId}/edit-locks/renew`, { method: 'POST', body: JSON.stringify({ token: entry.lease.token, session_id: editSessionId }) }).then((next) => { if (held.get(id) === entry) entry.lease = renewedLease(entry.lease, next) }).catch((cause) => { if (held.get(id) !== entry) return; entry.lost = cause instanceof Error ? cause.message : '编辑占用已失效'; clearInterval(entry.timer); emit() })
      }, 30_000) }
      held.set(id, entry); emit(); return entry
    })()
    pending.set(id, promise)
  }
  try { const entry = await promise; entry.refs += 1; return entry.lease.token } finally { if (pending.get(id) === promise) pending.delete(id) }
}
/** 释放当前页面/写请求的一份引用，最后一份退出时归还占用。 */
export async function releaseEditLock(target: EditTarget, all = false, expectedToken?: string): Promise<void> {
  const id = key(target); const entry = held.get(id)
  if (!entry) return
  if (expectedToken && !isSameLease(entry.lease, expectedToken)) return
  entry.refs = all ? 0 : entry.refs - 1
  if (entry.refs > 0) return
  clearInterval(entry.timer); held.delete(id); emit()
  await request(`/api/teams/${entry.teamId}/edit-locks/release`, { method: 'POST', body: JSON.stringify({ token: entry.lease.token, session_id: editSessionId }), keepalive: true }).catch(() => undefined)
}
/** 切换空间和离开标签页时尽早释放；异常退出仍有后端租约过期兜底。 */
subscribeWorkspace(() => { pending.clear(); for (const entry of held.values()) entry.lost = '空间或登录身份已变化，请重新读取内容后取得编辑权'; emit(); for (const entry of held.values()) void releaseEditLock(entry.target, true) })
if (typeof window !== 'undefined') window.addEventListener('pagehide', () => { for (const entry of held.values()) void releaseEditLock(entry.target, true) })

/** 补齐已知分集的业务集号，禁止用当前项目猜测归属。 */
async function episodeTarget(id: number): Promise<EditTarget> {
  let info = episodes.get(id)
  if (!info) { const episode = await request<Row>(`/api/drama/episodes/${id}`); recordDramaResponse(`/api/drama/episodes/${id}`, episode); info = episodes.get(id) }
  if (!info) throw new Error('无法确认分集归属，请重新打开项目')
  return { projectId: info.projectId, type: 'episode', resourceId: info.number }
}
/** 根据现有写入接口解析唯一资源目标。 */
export async function resolveEditTarget(path: string, init: RequestInit): Promise<EditTarget | null> {
  if (!init.method || init.method === 'GET' || /cancel_|\/route$/.test(path)) return null
  const url = new URL(path, 'http://local')
  const body: Row = typeof init.body === 'string' ? JSON.parse(init.body || '{}') : {}
  let projectId = Number(body.project_id || url.searchParams.get('project_id'))
  let match = path.match(/\/projects\/(\d+)/)
  if (match) return { projectId: Number(match[1]), type: 'structure', resourceId: 0 }
  match = path.match(/\/scripts\/(\d+)(?:\/episodes\/(\d+))?/)
  if (match) return { projectId: Number(match[1]), type: match[2] ? 'episode' : 'script', resourceId: Number(match[2] || 0) }
  match = path.match(/\/episodes\/(\d+)/)
  if (match) return episodeTarget(Number(match[1]))
  match = path.match(/\/fragments\/(\d+)/)
  if (match) { const episodeId = fragments.get(Number(match[1])); if (!episodeId) throw new Error('请刷新分集后再操作此分镜'); return episodeTarget(episodeId) }
  match = path.match(/\/assets\/(\d+)/)
  if (match) { const assetId = Number(match[1]); projectId = assets.get(assetId) || 0; if (!projectId) throw new Error('请重新打开所属项目的资产后再编辑'); return { projectId, type: 'asset', resourceId: assetId } }
  if (!projectId) return null
  if (/canvas/.test(path)) return { projectId, type: 'canvas', resourceId: 0 }
  if (/seed_from_script|script_summary|add_episode/.test(path)) return { projectId, type: 'structure', resourceId: 0 }
  if (/episode_script|confirm_from_script/.test(path)) return { projectId, type: body.episode_number ? 'episode' : 'structure', resourceId: Number(body.episode_number || 0) }
  if (/\/generation\/voice$/.test(url.pathname)) return { projectId, type: 'structure', resourceId: 0 }
  if (/assets|generation\//.test(path)) return { projectId, type: 'asset', resourceId: Number(body.asset_id || body.character_asset_id || 0) }
  return null
}
/** 对团队写入附加 fencing token；个人请求原样通过。 */
export async function withDramaEditLock<T>(path: string, init: RequestInit | undefined, action: (init?: RequestInit) => Promise<T>): Promise<T> {
  const epoch = workspaceScope.epoch
  const authToken = localStorage.getItem('token')
  const target = init ? await resolveEditTarget(path, init) : null
  if (!target) return action(init)
  const actualTeam = await projectTeam(target.projectId)
  if (epoch !== workspaceScope.epoch || authToken !== localStorage.getItem('token')) throw new DOMException('空间或登录身份已变化', 'AbortError')
  const scopeHeaders = new Headers(init?.headers)
  if (actualTeam === null) scopeHeaders.set('X-Workspace-Personal', '1')
  else scopeHeaders.set('X-Team-Id', String(actualTeam))
  const scoped = { ...init, headers: scopeHeaders }
  const heldToken = await holdEditLock(target)
  if (!heldToken) return action(scoped)
  try {
    const entry = held.get(key(target))
    if (!entry || !isSameLease(entry.lease, heldToken) || entry.lost || new Date(entry.lease.expires_at).getTime() <= Date.now()) throw new Error(entry?.lost || '编辑占用已失效，请重新取得编辑权')
    const headers = new Headers(scopeHeaders)
    headers.set('X-Edit-Session', editSessionId); headers.set('X-Edit-Token', entry.lease.token); headers.set('X-Edit-Version', String(entry.lease.version))
    return await action({ ...init, headers })
  } catch (cause) {
    const error = cause as Error & { status?: number }
    if (error.status === 423 || error.status === 428 || (error.status === 409 && /编辑版本|占用|编辑权/.test(error.message))) {
      const entry = held.get(key(target))
      if (entry && isSameLease(entry.lease, heldToken)) { entry.lost = error.message; clearInterval(entry.timer); emit() }
    }
    throw cause
  } finally { await releaseEditLock(target, false, heldToken) }
}
