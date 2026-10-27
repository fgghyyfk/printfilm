import { useEffect, useState } from 'react'
import { getToken } from '@/lib/auth'

export type Item = { id: number; version?: number; [key: string]: unknown }
export type Listing = { items: Item[]; meta: { page: number; page_size: number; total: number } }

// 管理端使用原管理员 token，但下游 API 完全隔离。
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, { method, headers: { Authorization: `Bearer ${getToken() ?? ''}`, ...(body !== undefined ? { 'Content-Type': body instanceof Blob ? 'application/octet-stream' : 'application/json' } : {}) }, body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body) })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : value.detail?.message || '请求失败，请检查输入与登录状态')
  return value as T
}

/** 管理端在线打开授权函，不把 OSS 地址交给浏览器直接下载。 */
export async function letterFile(id: number): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(`/api/marketplace/authorizations/${id}/file`, { headers: { Authorization: `Bearer ${getToken() ?? ''}` } })
  } catch {
    throw new Error('授权函暂时无法打开')
  }
  if (!response.ok) {
    const value = await response.json().catch(() => ({}))
    throw new Error(typeof value.detail === 'string' ? value.detail : '授权函暂时无法打开')
  }
  return response.blob()
}

// 管理端菜单也由服务端能力发现控制，不另造前端开关。
export function useMarketplaceEnabled() {
  const [enabled, setEnabled] = useState(false)
  useEffect(() => { let active = true; request<{ enabled: boolean }>('/api/marketplace/capabilities').then(value => { if (active) setEnabled(value.enabled) }).catch(() => {}); return () => { active = false } }, [])
  return enabled
}

// 管理员签名必须走原身份鉴权，不拼接 OSS 地址。
export async function assetUrl(id: number, purpose = 'original') {
  return (await request<{ url: string }>(`/api/marketplace/media/asset/${id}/${purpose}`)).url
}
