/** 框架的通用 404 表示接口未加载；业务 404 则保留会话不存在等真实原因。 */
export function creationApiErrorDetail(status: number, detail: unknown): unknown {
  if (status !== 404) return detail
  if (typeof detail === 'string' && detail.trim().toLowerCase() === 'not found')
    return '创作服务接口尚未加载，请重启后端服务后重试。'
  if (detail === '创作会话不存在')
    return '创作会话不存在或已被删除，请返回我的短片或新建对话。'
  return detail
}
