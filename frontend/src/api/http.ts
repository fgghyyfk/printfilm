/** HTTP 基础能力：地址、登录态和响应解析；不依赖浏览器全局配置或业务模块。 */

/** 将 FastAPI 的字符串或校验错误数组转换为可展示文案。 */
export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail || fallback
  if (Array.isArray(detail)) {
    const message = detail.map((item) => {
      if (item && typeof item === 'object' && 'msg' in item) {
        return String(item.msg || JSON.stringify(item))
      }
      return JSON.stringify(item)
    }).join('; ')
    return message || fallback
  }
  return fallback
}

/** 未配置时使用当前主机的开发 API；显式空字符串保留同源代理语义。 */
export function resolveApiBase(
  configuredBase: unknown,
  location?: { protocol: string; hostname: string },
): string {
  if (configuredBase !== undefined && configuredBase !== null) return String(configuredBase)
  return location?.hostname
    ? `${location.protocol}//${location.hostname}:8000`
    : 'http://127.0.0.1:8000'
}

/** 不同业务沿用原有错误文案和计费提示，由调用方决定错误展示策略。 */
export type ApiErrorHandler = (response: Response, detail: unknown) => never

/** 创建可注入登录态的 HTTP 客户端，JSON 与文件上传共用同一请求路径。 */
export function createHttpTransport(options: {
  baseUrl: string
  getToken: () => string | null
  onError: ApiErrorHandler
}) {
  /** 保留 Headers 的全部合法输入形式，FormData 的 boundary 交给浏览器生成。 */
  async function response(
    path: string,
    init?: RequestInit,
    onError: ApiErrorHandler = options.onError,
  ): Promise<Response> {
    /* 每次请求重新读取 token，登录、登出后无需重建客户端。 */
    const headers = new Headers()
    const token = options.getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json')
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    const result = await fetch(`${options.baseUrl}${path}`, { ...init, headers })
    if (!result.ok) {
      const error = await result.json().catch(() => ({ detail: result.statusText }))
      onError(result, error?.detail)
    }
    return result
  }

  /** 解析成功的 JSON 响应；下载等非 JSON 请求直接使用 response。 */
  async function json<T>(
    path: string,
    init?: RequestInit,
    onError?: ApiErrorHandler,
  ): Promise<T> {
    return (await response(path, init, onError)).json() as Promise<T>
  }

  return { response, json }
}
