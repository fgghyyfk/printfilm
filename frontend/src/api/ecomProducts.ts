/** 「我的商品」API：/api/tools/ecom-products/* */

import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'

const BASE = '/api/tools/ecom-products'

/** 商品资料 */
export type EcomProduct = {
  id: number
  name: string
  category: string
  size: string
  material: string
  material_label: string
  notes: string
  colors: { name: string; image_url: string }[]
  fabric_image_url: string
  created_at: string
}

/** 新建商品：颜色款图片与颜色名一一对应 */
export type EcomProductCreate = {
  name: string
  category: string
  size: string
  material: string
  notes: string
  colors: { name: string; file: File }[]
  fabric?: File | null
}

// 统一错误：401 抛「未登录」，其它取后端中文短句
function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  return createJsonRequester((response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    throw new Error(apiErrorMessage(detail, fallback))
  })<T>(`${BASE}${path}`, init)
}

/** 我的商品（服务端分页） */
export function listEcomProducts(page = 1, pageSize = 20, signal?: AbortSignal): Promise<{ items: EcomProduct[]; total: number }> {
  return request(`?page=${page}&page_size=${pageSize}`, '加载商品失败', { signal })
}

/** 商品详情 */
export function getEcomProduct(id: number, signal?: AbortSignal): Promise<EcomProduct> {
  return request(`/${id}`, '加载商品失败', { signal })
}

/** 新建商品 */
export function createEcomProduct(payload: EcomProductCreate): Promise<EcomProduct> {
  const body = new FormData()
  body.append('name', payload.name)
  body.append('category', payload.category)
  body.append('size', payload.size)
  body.append('material', payload.material)
  body.append('notes', payload.notes)
  body.append('color_names', JSON.stringify(payload.colors.map((c) => c.name)))
  for (const c of payload.colors) body.append('files', c.file)
  if (payload.fabric) body.append('fabric', payload.fabric)
  return request('', '保存商品失败', { method: 'POST', body })
}

/** 改商品资料 */
export function updateEcomProduct(id: number, patch: Partial<Pick<EcomProduct, 'name' | 'category' | 'size' | 'material' | 'notes'>> & { color_names?: string[] }): Promise<EcomProduct> {
  return request(`/${id}`, '保存失败', { method: 'PUT', body: JSON.stringify(patch) })
}

/** 追加颜色款 */
export function addEcomProductColor(id: number, name: string, file: File): Promise<EcomProduct> {
  const body = new FormData()
  body.append('name', name)
  body.append('file', file)
  return request(`/${id}/colors`, '添加颜色失败', { method: 'POST', body })
}

/** 删除颜色款 */
export function removeEcomProductColor(id: number, index: number): Promise<EcomProduct> {
  return request(`/${id}/colors/${index}`, '删除颜色失败', { method: 'DELETE' })
}

/** 设置面料实拍 */
export function setEcomProductFabric(id: number, file: File): Promise<EcomProduct> {
  const body = new FormData()
  body.append('file', file)
  return request(`/${id}/fabric`, '上传面料图失败', { method: 'PUT', body })
}

/** 删除商品 */
export function deleteEcomProduct(id: number): Promise<{ ok: boolean }> {
  return request(`/${id}`, '删除失败', { method: 'DELETE' })
}
