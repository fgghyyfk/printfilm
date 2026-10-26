/** 当前请求空间只存在内存中，切换时取消旧请求，避免响应串入新空间。 */
let controller = new AbortController()
export const workspaceScope = { teamId: null as number | null, signal: controller.signal, epoch: 0 }
const listeners = new Set<() => void>()
/** 切换团队；个人空间始终使用 null。 */
export function setWorkspaceTeam(teamId: number | null, reset = false): void {
  if (workspaceScope.teamId === teamId && !reset) return
  controller.abort(new DOMException('创作空间或登录身份已切换', 'AbortError')); controller = new AbortController()
  workspaceScope.teamId = teamId; workspaceScope.signal = controller.signal
  workspaceScope.epoch += 1
  listeners.forEach((listener) => listener())
}
/** 订阅真实资源归属带来的空间变化。 */
export function subscribeWorkspace(listener: () => void): () => void { listeners.add(listener); return () => { listeners.delete(listener) } }
/** 仅为创作接口附加空间，按 ID 读取项目时由响应确认真实归属。 */
export function scopedRequestInit(path: string, init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers)
  const personal = headers.get('X-Workspace-Personal') === '1'
  headers.delete('X-Workspace-Personal')
  const creative = /^\/api\/(drama|tasks)(\/|$)/.test(path)
  const ownershipRead = (!init.method || init.method === 'GET') && /^\/api\/drama\/projects\/\d+(\?|$)/.test(path)
  if (creative && !ownershipRead && !personal && !headers.has('X-Team-Id') && workspaceScope.teamId !== null) headers.set('X-Team-Id', String(workspaceScope.teamId))
  const signal = init.signal && typeof AbortSignal.any === 'function' ? AbortSignal.any([init.signal, workspaceScope.signal]) : init.signal || workspaceScope.signal
  return { ...init, headers, signal }
}
