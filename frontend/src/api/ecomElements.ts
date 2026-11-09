/** 电商图元素库 API：/api/tools/ecom-elements/* */

import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'

const BASE = '/api/tools/ecom-elements'

/** 一个画面元素（系统预置或我的元素） */
export type EcomElement = {
  key: string
  name: string
  category: string
  scope: 'system' | 'user'
  prompt: string
  scale: string
  image_url: string
  industries: string[]
}

export type EcomElementList = { items: EcomElement[]; total: number; categories: { id: string; name: string }[] }

// 统一错误：401 抛「未登录」，其它取后端中文短句
function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  return createJsonRequester((response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    throw new Error(apiErrorMessage(detail, fallback))
  })<T>(`${BASE}${path}`, init)
}

/** 元素列表（服务端分页与筛选） */
export function listEcomElements(params: { category?: string; q?: string; industry?: string; mine?: boolean; pageSize?: number },
  signal?: AbortSignal): Promise<EcomElementList> {
  const qs = new URLSearchParams()
  if (params.category) qs.set('category', params.category)
  if (params.q) qs.set('q', params.q)
  if (params.industry) qs.set('industry', params.industry)
  if (params.mine) qs.set('mine', 'true')
  qs.set('page_size', String(params.pageSize || 120))
  return request(`?${qs}`, '加载元素失败', { signal })
}

/** 按商品资料与行业推荐元素 */
export function recommendEcomElements(productId: number | null, industry: string, signal?: AbortSignal): Promise<{ items: EcomElement[] }> {
  const qs = new URLSearchParams()
  if (productId) qs.set('product_id', String(productId))
  if (industry) qs.set('industry', industry)
  return request(`/recommend?${qs}`, '加载推荐元素失败', { signal })
}

/** 新建我的元素：一句话描述，可附实物照片 */
export function createEcomElement(payload: { name: string; category: string; prompt?: string; file?: File | null }): Promise<EcomElement> {
  const body = new FormData()
  body.append('name', payload.name)
  body.append('category', payload.category)
  body.append('prompt', payload.prompt || '')
  if (payload.file) body.append('file', payload.file)
  return request('', '新建元素失败', { method: 'POST', body })
}

/** 删除我的元素 */
export function deleteEcomElement(key: string): Promise<{ ok: boolean }> {
  return request(`/${encodeURIComponent(key)}`, '删除失败', { method: 'DELETE' })
}
