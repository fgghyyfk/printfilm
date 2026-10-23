/** API Key 管理客户端（/api/user/api-keys） */

import { API_BASE, createJsonRequester } from './client'
import { apiErrorMessage } from './http'

/** API Key 错误仅交由当前页面展示，不触发计费弹窗。 */
const request = createJsonRequester((response, detail) => {
  throw new Error(apiErrorMessage(detail, response.statusText) || '请求失败')
})

export type ApiKeyItem = {
  id: number
  name: string
  key_prefix: string
  created_at: string | null
  last_used_at: string | null
}

export type ApiKeyCreated = ApiKeyItem & {
  secret: string
}

/** 返回开放 API 调用示例使用的服务根地址。 */
export function getPublicApiBase() {
  return API_BASE
}

export const apiKeysApi = {
  list() {
    return request<ApiKeyItem[]>('/api/user/api-keys')
  },
  create(name: string) {
    return request<ApiKeyCreated>('/api/user/api-keys', {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
  },
  revoke(id: number) {
    return request<{ ok: boolean }>(`/api/user/api-keys/${id}`, { method: 'DELETE' })
  },
}
