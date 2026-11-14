/** 用户资料请求失败只在当前凭证明确过期时结束会话，临时故障不影响登录。 */
export async function loadSessionUser<T>(options: {
  loadUser: () => Promise<T>
  getToken: () => string | null
  isCurrent: () => boolean
  onUser: (user: T) => void
  onUnauthorized: () => void
}): Promise<void> {
  const token = options.getToken()
  if (!token) return
  /** 账号事件、卸载和新登录都使旧响应失效。 */
  const isCurrentSession = () => options.isCurrent() && token === options.getToken()
  try {
    const user = await options.loadUser()
    if (isCurrentSession()) options.onUser(user)
  } catch (cause) {
    if (isCurrentSession() && cause !== null && typeof cause === 'object' && 'status' in cause && cause.status === 401) options.onUnauthorized()
  }
}
