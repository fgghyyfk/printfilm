/** 「我的商品」API：/api/tools/ecom-products/* */

import { createJsonRequester, requestResponse } from './client'
import { apiErrorMessage } from './http'

const BASE = '/api/tools/ecom-products'

/** 商品资料 */
export type EcomProduct = {
  id: number
  name: string
  category: string
  size: string
  /** 实际尺寸（厘米）：高 / 宽 / 厚，可缺项 */
  dims?: EcomDims
  material: string
  material_label: string
  notes: string
  /** photo_image_url：抠过图时保留的上传原图；transparent：当前商品图是否透明底（老数据为 null） */
  colors: { name: string; image_url: string; real_image_url?: string; photo_image_url?: string; transparent?: boolean | null }[]
  fabric_image_url: string
  /** 参考详情图 */
  references?: { url: string; kind: 'own' | 'competitor' }[]
  /** 商品资料（未整理为 null） */
  knowledge?: EcomKnowledge | null
  created_at: string
}

/** 商品实际尺寸（厘米） */
export type EcomDims = { h?: number; w?: number; d?: number }

/** 新建商品：颜色款图片与颜色名一一对应 */
export type EcomProductCreate = {
  name: string
  category: string
  size: string
  material: string
  notes: string
  colors: { name: string; file: File; real?: File | null }[]
  fabric?: File | null
  dims?: EcomDims
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
  appendDims(body, payload.dims)
  const reals = payload.colors.map((c, i) => ({ file: c.real, i })).filter((x): x is { file: File; i: number } => Boolean(x.file))
  for (const r of reals) body.append('real_files', r.file)
  if (reals.length) body.append('real_for', JSON.stringify(reals.map((r) => r.i)))
  return request('', '保存商品失败', { method: 'POST', body })
}

/** 把尺寸写进表单（dim_h / dim_w / dim_d，空项不传） */
export function appendDims(body: FormData, dims?: EcomDims) {
  for (const k of ['h', 'w', 'd'] as const) if (dims?.[k]) body.append(`dim_${k}`, String(dims[k]))
}

/** 改商品资料 */
export function updateEcomProduct(id: number, patch: Partial<Pick<EcomProduct, 'name' | 'category' | 'size' | 'material' | 'notes' | 'dims'>> & { color_names?: string[] }): Promise<EcomProduct> {
  return request(`/${id}`, '保存失败', { method: 'PUT', body: JSON.stringify(patch) })
}

/** 追加颜色款 */
export function addEcomProductColor(id: number, name: string, file: File): Promise<EcomProduct> {
  const body = new FormData()
  body.append('name', name)
  body.append('file', file)
  return request(`/${id}/colors`, '添加颜色失败', { method: 'POST', body })
}

/** 设置颜色款的实物图 */
export function setEcomProductColorReal(id: number, index: number, file: File): Promise<EcomProduct> {
  const body = new FormData()
  body.append('file', file)
  return request(`/${id}/colors/${index}/real`, '上传实物图失败', { method: 'PUT', body })
}

/** 取颜色款原图（同源接口，供浏览器抠图读取像素） */
export async function fetchEcomProductColorPhoto(id: number, index: number): Promise<Blob> {
  const res = await requestResponse(`${BASE}/${id}/colors/${index}/photo`, undefined, (response) => {
    throw new Error(response.status === 401 ? '未登录' : '读取商品原图失败')
  })
  return res.blob()
}

/** 保存浏览器抠好的透明底 PNG（原图保留，可切回） */
export function saveEcomProductColorCutout(id: number, index: number, png: Blob): Promise<EcomProduct> {
  const body = new FormData()
  body.append('file', png, 'cutout.png')
  return request(`/${id}/colors/${index}/cutout`, '保存抠图失败', { method: 'PUT', body })
}

/** 不用抠图，切回上传的原图 */
export function restoreEcomProductColor(id: number, index: number): Promise<EcomProduct> {
  return request(`/${id}/colors/${index}/cutout`, '切回原图失败', { method: 'DELETE' })
}

/** 需要自动抠图的颜色款下标：日化 / 标品、上传的不是透明底且还没抠过 */
export function colorsToCutout(p: EcomProduct): number[] {
  if (p.category !== 'goods') return []
  return p.colors.flatMap((c, i) => (c.transparent === false && !c.photo_image_url ? [i] : []))
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

/** 商品资料（读参考详情整理 + 用户修改） */
export type EcomKnowledge = {
  summary: string
  en_name: string
  story: string
  selling_points: { title: string; desc: string }[]
  ingredients: { name: string; desc: string }[]
  variants: { name: string; top: string; middle: string; base: string; desc: string }[]
  specs: { label: string; value: string }[]
  faq: { q: string; a: string }[]
  usage: string[]
  scenarios: string[]
  cautions: string[]
  outline: string[]
  angles: string[]
  warnings?: string[]
  updated_at?: string
}

/** 上传参考详情图：own 品牌自己的详情（提取事实），competitor 竞品（只学结构） */
export function addEcomProductReferences(id: number, kind: 'own' | 'competitor', files: File[]): Promise<EcomProduct> {
  const body = new FormData()
  body.append('kind', kind)
  for (const f of files) body.append('files', f)
  return request(`/${id}/references`, '上传参考详情失败', { method: 'POST', body })
}

/** 删除一张参考详情图 */
export function removeEcomProductReference(id: number, index: number): Promise<EcomProduct> {
  return request(`/${id}/references/${index}`, '删除失败', { method: 'DELETE' })
}

/** AI 读参考详情整理商品资料（按读图批次计费） */
export function extractEcomProductKnowledge(id: number): Promise<EcomProduct> {
  return request(`/${id}/knowledge/extract`, '整理资料失败', { method: 'POST' })
}

/** 保存修改后的商品资料 */
export function saveEcomProductKnowledge(id: number, knowledge: Partial<EcomKnowledge>): Promise<EcomProduct> {
  return request(`/${id}/knowledge`, '保存资料失败', { method: 'PUT', body: JSON.stringify(knowledge) })
}
